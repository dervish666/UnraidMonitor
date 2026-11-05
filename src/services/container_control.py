import asyncio
import inspect
import logging
from typing import Any

import docker
import docker.api.network
from src.services.docker_client import DockerClientLike
from docker.types import DeviceRequest

logger = logging.getLogger(__name__)

# Keyword arguments the installed docker-py accepts when connecting a container
# to a network. Network.connect() forwards **kwargs to this method, which
# rejects anything else with a TypeError.
_CONNECT_KWARGS: frozenset[str] = frozenset(
    name
    for name in inspect.signature(
        docker.api.network.NetworkApiMixin.connect_container_to_network
    ).parameters
    if name not in ("self", "container", "net_id")
)


def _endpoint_connect_kwargs(endpoint: dict[str, Any], own_short_id: str) -> dict[str, Any]:
    """Translate a ``docker inspect`` network endpoint into ``Network.connect()`` kwargs.

    Inspect reports the endpoint in API shape (``Aliases``, ``Links``,
    ``IPAMConfig``, ``DriverOpts``); docker-py takes snake_case keywords and
    builds that shape itself. Passing the inspect keys through raised
    "unexpected keyword argument 'Aliases'", so every secondary network with
    an alias or a static IP was dropped after a /pull.

    ``MacAddress`` is left out: inspect reports the address Docker assigned
    even when nobody chose one, and pinning it would carry it to the new
    container as if it had been configured.
    """
    kwargs: dict[str, Any] = {}

    # Engines before API 1.45 also list the container's short ID as an alias.
    # It names the old container, so it is not carried over.
    aliases = [
        a for a in (endpoint.get("Aliases") or [])
        if isinstance(a, str) and a and a != own_short_id
    ]
    if aliases:
        kwargs["aliases"] = aliases

    # Inspect gives "name:alias" strings; docker-py wants (name, alias) pairs
    # and unpacks each item, so a plain string fails in normalize_links().
    links: list[tuple[str, str | None]] = []
    for link in endpoint.get("Links") or []:
        if isinstance(link, str) and link:
            name, _, alias = link.partition(":")
            links.append((name, alias or None))
    if links:
        kwargs["links"] = links

    ipam = endpoint.get("IPAMConfig") or {}
    if ipam.get("IPv4Address"):
        kwargs["ipv4_address"] = ipam["IPv4Address"]
    if ipam.get("IPv6Address"):
        kwargs["ipv6_address"] = ipam["IPv6Address"]
    if ipam.get("LinkLocalIPs"):
        kwargs["link_local_ips"] = list(ipam["LinkLocalIPs"])

    if endpoint.get("DriverOpts"):
        kwargs["driver_opt"] = dict(endpoint["DriverOpts"])

    unsupported = set(kwargs) - _CONNECT_KWARGS
    for name in unsupported:
        logger.warning(f"Installed docker-py cannot set {name} on a network endpoint; skipping it")
        del kwargs[name]
    return kwargs


def _attached_networks(container: Any) -> set[str]:
    """Names of the networks a container is already on, per its inspect data."""
    attrs = getattr(container, "attrs", None)
    if not isinstance(attrs, dict):
        return set()
    networks = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
    return set(networks) if isinstance(networks, dict) else set()


# HostConfig fields copied straight across: inspect key -> docker-py kwarg.
_SIMPLE_HOST_FIELDS: dict[str, str] = {
    "CpusetCpus": "cpuset_cpus",
    "CpusetMems": "cpuset_mems",
    "CpuPeriod": "cpu_period",
    "CpuQuota": "cpu_quota",
    "CpuRealtimePeriod": "cpu_rt_period",
    "CpuRealtimeRuntime": "cpu_rt_runtime",
    "BlkioWeight": "blkio_weight",
    "BlkioWeightDevice": "blkio_weight_device",
    "BlkioDeviceReadBps": "device_read_bps",
    "BlkioDeviceWriteBps": "device_write_bps",
    "BlkioDeviceReadIOps": "device_read_iops",
    "BlkioDeviceWriteIOps": "device_write_iops",
    "MemorySwap": "memswap_limit",
    "MemorySwappiness": "mem_swappiness",
    "OomKillDisable": "oom_kill_disable",
    "OomScoreAdj": "oom_score_adj",
    "PublishAllPorts": "publish_all_ports",
    "UTSMode": "uts_mode",
    "UsernsMode": "userns_mode",
    "CgroupParent": "cgroup_parent",
    "AutoRemove": "auto_remove",
    "VolumeDriver": "volume_driver",
    "StorageOpt": "storage_opt",
    "DeviceCgroupRules": "device_cgroup_rules",
    "DnsOptions": "dns_opt",
}

# HostConfig fields _extract_run_config carries itself.
_HANDLED_HOST_FIELDS: frozenset[str] = frozenset({
    "Binds", "Mounts", "ReadonlyRootfs", "VolumesFrom", "PidsLimit", "Init",
    "PortBindings", "RestartPolicy", "NetworkMode", "Privileged", "CapAdd",
    "CapDrop", "Devices", "DeviceRequests", "Runtime", "GroupAdd", "Dns",
    "DnsSearch", "ExtraHosts", "LogConfig", "Tmpfs", "Ulimits", "Sysctls",
    "SecurityOpt", "PidMode", "IpcMode", "ShmSize", "NanoCpus", "CpuShares",
    "Memory", "MemoryReservation", "Links",
}) | frozenset(_SIMPLE_HOST_FIELDS)

# Filled in by the daemon (or Windows-only), not chosen by the user; a fresh
# container gets the same values on its own.
_DAEMON_HOST_FIELDS: frozenset[str] = frozenset({
    "ConsoleSize", "CgroupnsMode", "Isolation", "ContainerIDFile", "MaskedPaths",
    "ReadonlyPaths", "KernelMemory", "KernelMemoryTCP", "Cgroup", "CpuCount",
    "CpuPercent", "IOMaximumIOps", "IOMaximumBandwidth",
})

_EMPTY_VALUES: tuple[Any, ...] = (None, "", 0, False, [], {})


def unsupported_recreate_settings(attrs: dict[str, Any]) -> list[str]:
    """HostConfig settings a /pull recreate would silently drop.

    Checked before the original is stopped. Anything listed here is refused,
    because a replacement that quietly loses a setting (a memory cap, a
    capability, a mount) looks healthy and is not the container you had.
    """
    host_config = attrs.get("HostConfig") or {}
    return sorted(
        key for key, value in host_config.items()
        if key not in _HANDLED_HOST_FIELDS
        and key not in _DAEMON_HOST_FIELDS
        and value not in _EMPTY_VALUES
    )


def _primary_endpoint_config(attrs: dict[str, Any]) -> dict[str, Any]:
    """API-shape endpoint config for the primary network, or {} if none needed.

    Only user-defined networks take aliases or a static address; the built-in
    modes (bridge, host, none, container:x) are recreated by name alone.
    """
    mode = (attrs.get("HostConfig") or {}).get("NetworkMode") or ""
    if mode in ("", "default", "bridge", "host", "none") or mode.startswith("container:"):
        return {}
    networks = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
    endpoint = networks.get(mode) if isinstance(networks, dict) else None
    if not endpoint:
        return {}
    kw = _endpoint_connect_kwargs(endpoint, (attrs.get("Id") or "")[:12])
    config: dict[str, Any] = {}
    if kw.get("aliases"):
        config["Aliases"] = kw["aliases"]
    if kw.get("links"):
        config["Links"] = [f"{name}:{alias}" if alias else name for name, alias in kw["links"]]
    ipam = {
        api_key: kw[kwarg]
        for api_key, kwarg in (
            ("IPv4Address", "ipv4_address"),
            ("IPv6Address", "ipv6_address"),
            ("LinkLocalIPs", "link_local_ips"),
        )
        if kw.get(kwarg)
    }
    if ipam:
        config["IPAMConfig"] = ipam
    if kw.get("driver_opt"):
        config["DriverOpts"] = kw["driver_opt"]
    return config


def _mount_targets(attrs: dict[str, Any]) -> set[str]:
    """Container paths already covered by Binds or structured HostConfig.Mounts."""
    host_config = attrs.get("HostConfig") or {}
    covered: set[str] = set()
    for bind in host_config.get("Binds") or []:
        parts = str(bind).split(":")
        if len(parts) >= 2:
            covered.add(parts[1])
    for mount in host_config.get("Mounts") or []:
        if isinstance(mount, dict) and mount.get("Target"):
            covered.add(mount["Target"])
    return covered


def _unbound_volume_binds(attrs: dict[str, Any]) -> list[str]:
    """Bind strings naming volumes that Binds/HostConfig.Mounts do not cover.

    These are the anonymous volumes Docker made for an image's VOLUME lines.
    Naming them keeps the service on its existing data.
    """
    covered = _mount_targets(attrs)
    binds: list[str] = []
    for mount in attrs.get("Mounts") or []:
        if not isinstance(mount, dict) or mount.get("Type") != "volume":
            continue
        dest, name = mount.get("Destination"), mount.get("Name")
        if dest and name and dest not in covered:
            binds.append(f"{name}:{dest}:{'rw' if mount.get('RW', True) else 'ro'}")
    return binds


def _resolved_host_mounts(attrs: dict[str, Any]) -> list[dict[str, Any]]:
    """HostConfig.Mounts with anonymous volume sources filled in by identity."""
    resolved = {
        m.get("Destination"): m.get("Name")
        for m in attrs.get("Mounts") or []
        if isinstance(m, dict) and m.get("Type") == "volume"
    }
    mounts: list[dict[str, Any]] = []
    for mount in (attrs.get("HostConfig") or {}).get("Mounts") or []:
        mount = dict(mount)
        if mount.get("Type") == "volume" and not mount.get("Source"):
            name = resolved.get(mount.get("Target"))
            if name:
                mount["Source"] = name
        mounts.append(mount)
    return mounts


def _legacy_links(links: list[str]) -> dict[str, str]:
    """HostConfig.Links ("/db:/app/db") to docker-py's {name: alias}."""
    result: dict[str, str] = {}
    for link in links:
        name, _, alias = str(link).partition(":")
        name = name.lstrip("/")
        if name:
            result[name] = alias.rsplit("/", 1)[-1] or name
    return result


class ContainerController:
    """Controls Docker containers with protection support."""

    def __init__(
        self,
        docker_client: DockerClientLike,
        protected_containers: list[str],
    ) -> None:
        self.docker_client = docker_client
        self.protected_containers = set(protected_containers)

    def is_protected(self, container_name: str) -> bool:
        """Check if container is protected from control commands."""
        return container_name in self.protected_containers

    async def restart(self, container_name: str) -> str:
        """Restart a container. Returns a status message."""
        try:
            def _do_restart() -> None:
                container = self.docker_client.containers.get(container_name)
                container.restart()

            await asyncio.to_thread(_do_restart)
            logger.info(f"Restarted container: {container_name}")
            return f"✅ {container_name} restarted successfully"
        except docker.errors.NotFound:
            return f"❌ Container '{container_name}' not found"
        except Exception as e:
            logger.error(f"Failed to restart {container_name}: {e}", exc_info=True)
            return f"❌ Failed to restart {container_name}. Check logs for details."

    async def stop(self, container_name: str) -> str:
        """Stop a container. Returns a status message."""
        try:
            def _do_stop() -> bool:
                container = self.docker_client.containers.get(container_name)
                if container.status != "running":
                    return False
                container.stop()
                return True

            stopped = await asyncio.to_thread(_do_stop)
            if not stopped:
                return f"ℹ️ {container_name} is already stopped"
            logger.info(f"Stopped container: {container_name}")
            return f"✅ {container_name} stopped"
        except docker.errors.NotFound:
            return f"❌ Container '{container_name}' not found"
        except Exception as e:
            logger.error(f"Failed to stop {container_name}: {e}", exc_info=True)
            return f"❌ Failed to stop {container_name}. Check logs for details."

    async def start(self, container_name: str) -> str:
        """Start a container. Returns a status message."""
        try:
            def _do_start() -> bool:
                container = self.docker_client.containers.get(container_name)
                if container.status == "running":
                    return False
                container.start()
                return True

            started = await asyncio.to_thread(_do_start)
            if not started:
                return f"ℹ️ {container_name} is already running"
            logger.info(f"Started container: {container_name}")
            return f"✅ {container_name} started"
        except docker.errors.NotFound:
            return f"❌ Container '{container_name}' not found"
        except Exception as e:
            logger.error(f"Failed to start {container_name}: {e}", exc_info=True)
            return f"❌ Failed to start {container_name}. Check logs for details."

    async def pull_and_recreate(self, container_name: str) -> str:
        """Pull latest image and recreate container. Returns a status message.

        The original is never deleted until its replacement is running:
        1. Refuse, before touching anything, if inspect shows settings the
           extractor cannot carry (a silent drop is worse than no update).
        2. Pull the new image (no changes to the running container).
        3. Stop the original and rename it aside.
        4. Create the replacement (primary network endpoint included), join
           its secondary networks, then start it. Create and start are
           separate calls so a start failure leaves a known container to
           remove, instead of a half-made one blocking the name.
        5. On any failure: remove only the replacement this call created,
           rename the original back and start it. Its networks, volumes and
           anonymous volumes were never detached.
        6. On success, remove the original.
        """
        try:
            def _get_container_info() -> tuple[Any, str, str | None]:
                container = self.docker_client.containers.get(container_name)
                try:
                    img = container.image
                    if img is not None:
                        image_name = img.tags[0] if img.tags else (img.id or "unknown")
                        old_image_id: str | None = img.id
                    else:
                        image_name = container.attrs.get("Config", {}).get("Image", "unknown")
                        old_image_id = None
                except docker.errors.ImageNotFound:
                    image_name = container.attrs.get("Config", {}).get("Image", "unknown")
                    old_image_id = None
                return container, image_name, old_image_id

            container, image_name, _old_image_id = await asyncio.to_thread(_get_container_info)
            attrs = container.attrs

            unsupported = unsupported_recreate_settings(attrs)
            if unsupported:
                logger.warning(
                    f"Refusing to recreate {container_name}: cannot carry {', '.join(unsupported)}"
                )
                return (
                    f"❌ Not updating {container_name}: it uses settings this bot can't carry "
                    f"over to a new container ({', '.join(unsupported)}). Container unchanged. "
                    f"Update it from the Unraid Docker tab instead."
                )

            # Step 2: Pull latest image BEFORE touching the running container
            logger.info(f"Pulling image for {container_name}: {image_name}")
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self.docker_client.images.pull, image_name),
                    timeout=300,  # 5 minute timeout for image pulls
                )
            except asyncio.TimeoutError:
                return f"❌ Timed out pulling image for {container_name} (>5 min). Container unchanged."

            run_config = self._extract_run_config(attrs)
            secondary_networks = self._get_secondary_networks(attrs)
            was_running = (attrs.get("State") or {}).get("Running", True) is not False
            backup_name = f"{container_name}-pre-update-{(attrs.get('Id') or 'x')[:8]}"

            # Step 3: stop and set the original aside under a new name.
            await asyncio.to_thread(container.stop)
            try:
                await asyncio.to_thread(container.rename, backup_name)
            except Exception as rename_err:
                logger.error(f"Could not rename {container_name} aside: {rename_err}", exc_info=True)
                if was_running:
                    await asyncio.to_thread(container.start)
                return f"❌ Failed to update {container_name}: could not set the old container aside. Container unchanged."

            async def _restore(reason: str) -> str:
                try:
                    await asyncio.to_thread(container.rename, container_name)
                    if was_running:
                        await asyncio.to_thread(container.start)
                except Exception as restore_err:
                    logger.error(
                        f"CRITICAL: could not restore {container_name} from {backup_name}: {restore_err}",
                        exc_info=True,
                    )
                    return (
                        f"❌ CRITICAL: {container_name} could not be updated or restored. "
                        f"The original is stopped as `{backup_name}`; rename it back and start it."
                    )
                logger.info(f"Restored original {container_name} after failed update")
                return (
                    f"❌ Failed to update {container_name} ({reason}). "
                    f"The original container was restored unchanged. Check logs for details."
                )

            # Step 4: create the replacement.
            try:
                new_container = await asyncio.to_thread(
                    self.docker_client.containers.create,
                    image_name,
                    name=container_name,
                    **run_config,
                )
            except Exception as create_err:
                logger.error(f"Failed to create {container_name}: {create_err}", exc_info=True)
                return await _restore("could not create the new container")

            failed_networks: list[str] = []
            if secondary_networks:
                try:
                    attached = _attached_networks(
                        await asyncio.to_thread(self.docker_client.containers.get, container_name)
                    )
                except Exception:
                    attached = set()
                for net_name, endpoint in secondary_networks.items():
                    if net_name in attached:
                        # create() already joined it, e.g. NetworkMode "default"
                        # is reported as the "bridge" network.
                        continue
                    try:
                        network = await asyncio.to_thread(self.docker_client.networks.get, net_name)
                        await asyncio.to_thread(network.connect, new_container, **endpoint)
                    except Exception as net_err:
                        failed_networks.append(net_name)
                        logger.warning(
                            f"Failed to reconnect {container_name} to network {net_name}: {net_err}"
                        )

            try:
                await asyncio.to_thread(new_container.start)
            except Exception as start_err:
                logger.error(f"New {container_name} failed to start: {start_err}", exc_info=True)
                try:
                    # Only the container this call created; never the original.
                    await asyncio.to_thread(new_container.remove, force=True)
                except Exception as rm_err:
                    logger.error(f"Could not remove failed replacement for {container_name}: {rm_err}")
                    return (
                        f"❌ CRITICAL: the new {container_name} would not start and could not be "
                        f"removed. The original is stopped as `{backup_name}`."
                    )
                return await _restore("the new container would not start")

            # Step 6: the replacement is running; drop the original.
            leftover = ""
            try:
                await asyncio.to_thread(container.remove)
            except Exception as rm_err:
                logger.warning(f"Could not remove old {container_name} ({backup_name}): {rm_err}")
                leftover = f"\n⚠️ The old container is still there as `{backup_name}`; remove it when happy."

            logger.info(f"Recreated container: {container_name}")
            msg = f"✅ {container_name} updated (pulled {image_name} and recreated)"
            if failed_networks:
                msg += f"\n⚠️ Failed to reconnect networks: {', '.join(failed_networks)}"
            return msg + leftover

        except docker.errors.NotFound:
            return f"❌ Container '{container_name}' not found"
        except Exception as e:
            logger.error(f"Failed to pull and recreate {container_name}: {e}", exc_info=True)
            return f"❌ Failed to update {container_name}. Check logs for details."

    @staticmethod
    def _get_secondary_networks(attrs: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Extract secondary network connections (excluding the primary NetworkMode).

        Returns a dict of network_name -> keyword arguments for docker-py's
        ``Network.connect()``, for networks that need to be reconnected after
        container creation (since containers.run only connects the primary
        network).
        """
        host_config = attrs.get("HostConfig") or {}
        primary_network = host_config.get("NetworkMode", "")
        own_short_id = (attrs.get("Id") or "")[:12]

        networks = (attrs.get("NetworkSettings") or {}).get("Networks") or {}
        secondary = {}
        for net_name, net_config in networks.items():
            if net_name == primary_network:
                continue
            secondary[net_name] = _endpoint_connect_kwargs(net_config or {}, own_short_id)
        return secondary

    def _extract_run_config(self, attrs: dict[str, Any]) -> dict[str, Any]:
        """Extract comprehensive run configuration from container attributes.

        Extracts all significant container properties to ensure faithful recreation.
        """
        config = attrs.get("Config", {})
        host_config = attrs.get("HostConfig", {})
        run_config: dict[str, Any] = {}

        # From Config
        if config.get("Env"):
            run_config["environment"] = config["Env"]
        if config.get("Labels"):
            run_config["labels"] = config["Labels"]
        if config.get("Cmd"):
            run_config["command"] = config["Cmd"]
        if config.get("Entrypoint"):
            run_config["entrypoint"] = config["Entrypoint"]
        if config.get("WorkingDir"):
            run_config["working_dir"] = config["WorkingDir"]
        if config.get("User"):
            run_config["user"] = config["User"]
        if config.get("Healthcheck"):
            run_config["healthcheck"] = config["Healthcheck"]
        if config.get("StopSignal"):
            run_config["stop_signal"] = config["StopSignal"]
        if config.get("Hostname"):
            run_config["hostname"] = config["Hostname"]
        if config.get("Domainname"):
            run_config["domainname"] = config["Domainname"]
        if config.get("Tty"):
            run_config["tty"] = config["Tty"]
        if config.get("OpenStdin"):
            run_config["stdin_open"] = config["OpenStdin"]

        # From HostConfig. Volumes are carried by identity: an image-declared
        # VOLUME gets an anonymous volume, and recreating without naming it
        # would start the service on a fresh, empty one.
        volumes = list(host_config.get("Binds") or []) + _unbound_volume_binds(attrs)
        if volumes:
            run_config["volumes"] = volumes
        if host_config.get("Mounts"):
            run_config["mounts"] = _resolved_host_mounts(attrs)
        if host_config.get("ReadonlyRootfs"):
            run_config["read_only"] = True
        if host_config.get("VolumesFrom"):
            run_config["volumes_from"] = list(host_config["VolumesFrom"])
        if host_config.get("PidsLimit") and host_config["PidsLimit"] > 0:
            run_config["pids_limit"] = host_config["PidsLimit"]
        if host_config.get("Init"):
            run_config["init"] = True
        if host_config.get("Links"):
            run_config["links"] = _legacy_links(host_config["Links"])
        for api_key, kwarg in _SIMPLE_HOST_FIELDS.items():
            value = host_config.get(api_key)
            if value not in _EMPTY_VALUES:
                run_config[kwarg] = value
        if host_config.get("PortBindings"):
            run_config["ports"] = host_config["PortBindings"]
        if host_config.get("RestartPolicy"):
            run_config["restart_policy"] = host_config["RestartPolicy"]
        if host_config.get("NetworkMode"):
            mode = host_config["NetworkMode"]
            endpoint = _primary_endpoint_config(attrs)
            if endpoint:
                # A user-defined network (br0, a compose network) with a static
                # IP or aliases: set them at create time, or the replacement
                # comes up on a random address.
                run_config["network"] = mode
                run_config["networking_config"] = {mode: endpoint}
            else:
                run_config["network_mode"] = mode
        if config.get("MacAddress"):
            # Only present when someone set it; a Docker-assigned MAC lives in
            # NetworkSettings and is deliberately not pinned.
            run_config["mac_address"] = config["MacAddress"]
        if host_config.get("Privileged"):
            run_config["privileged"] = host_config["Privileged"]
        if host_config.get("CapAdd"):
            run_config["cap_add"] = host_config["CapAdd"]
        if host_config.get("CapDrop"):
            run_config["cap_drop"] = host_config["CapDrop"]
        if host_config.get("Devices"):
            run_config["devices"] = [
                f"{d['PathOnHost']}:{d['PathInContainer']}:{d.get('CgroupPermissions', 'rwm')}"
                for d in host_config["Devices"]
            ]
        if host_config.get("DeviceRequests"):
            # GPU access (nvidia et al). Distinct from Devices above, which covers
            # /dev passthrough like Intel QuickSync -- losing this silently brings
            # the container back healthy but without hardware acceleration.
            run_config["device_requests"] = [
                DeviceRequest(**req) for req in host_config["DeviceRequests"]
            ]
        if host_config.get("Runtime") and host_config["Runtime"] != "runc":
            run_config["runtime"] = host_config["Runtime"]
        if host_config.get("GroupAdd"):
            run_config["group_add"] = host_config["GroupAdd"]
        if host_config.get("Dns"):
            run_config["dns"] = host_config["Dns"]
        if host_config.get("DnsSearch"):
            run_config["dns_search"] = host_config["DnsSearch"]
        if host_config.get("ExtraHosts"):
            run_config["extra_hosts"] = host_config["ExtraHosts"]
        if host_config.get("LogConfig"):
            run_config["log_config"] = host_config["LogConfig"]
        if host_config.get("Tmpfs"):
            run_config["tmpfs"] = host_config["Tmpfs"]
        if host_config.get("Ulimits"):
            run_config["ulimits"] = host_config["Ulimits"]
        if host_config.get("Sysctls"):
            run_config["sysctls"] = host_config["Sysctls"]
        if host_config.get("SecurityOpt"):
            run_config["security_opt"] = host_config["SecurityOpt"]
        if host_config.get("PidMode"):
            run_config["pid_mode"] = host_config["PidMode"]
        if host_config.get("IpcMode") and host_config["IpcMode"] != "private":
            run_config["ipc_mode"] = host_config["IpcMode"]
        if host_config.get("ShmSize") and host_config["ShmSize"] != 67108864:  # != default 64MB
            run_config["shm_size"] = host_config["ShmSize"]

        # Resource limits
        if host_config.get("NanoCpus"):
            run_config["nano_cpus"] = host_config["NanoCpus"]
        if host_config.get("CpuShares") and host_config["CpuShares"] != 0:
            run_config["cpu_shares"] = host_config["CpuShares"]
        if host_config.get("Memory") and host_config["Memory"] != 0:
            run_config["mem_limit"] = host_config["Memory"]
        if host_config.get("MemoryReservation") and host_config["MemoryReservation"] != 0:
            run_config["mem_reservation"] = host_config["MemoryReservation"]

        return run_config
