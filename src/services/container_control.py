import asyncio
import logging
from typing import Any

import docker
from docker.types import DeviceRequest

logger = logging.getLogger(__name__)


class ContainerController:
    """Controls Docker containers with protection support."""

    def __init__(
        self,
        docker_client: docker.DockerClient,
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

        Uses a safe approach:
        1. Pull the new image first (no changes to running container)
        2. Save full container config
        3. Stop and remove old container
        4. Attempt recreation with new image
        5. If recreation fails, attempt rollback with old image
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

            container, image_name, old_image_id = await asyncio.to_thread(_get_container_info)

            # Step 1: Pull latest image BEFORE touching the running container
            logger.info(f"Pulling image for {container_name}: {image_name}")
            try:
                await asyncio.wait_for(
                    asyncio.to_thread(self.docker_client.images.pull, image_name),
                    timeout=300,  # 5 minute timeout for image pulls
                )
            except asyncio.TimeoutError:
                return f"❌ Timed out pulling image for {container_name} (>5 min). Container unchanged."

            # Step 2: Save full container config while still running
            attrs = container.attrs
            run_config = self._extract_run_config(attrs)
            secondary_networks = self._get_secondary_networks(attrs)

            # Step 3: Stop and remove old container
            await asyncio.to_thread(container.stop)
            await asyncio.to_thread(container.remove)

            # Step 4: Recreate container with new image
            try:
                await asyncio.to_thread(
                    self.docker_client.containers.run,  # type: ignore[arg-type]
                    image_name,
                    name=container_name,
                    detach=True,
                    **run_config,
                )
                # Reconnect secondary networks
                failed_networks: list[str] = []
                if secondary_networks:
                    new_container = await asyncio.to_thread(
                        self.docker_client.containers.get, container_name
                    )
                    for net_name, endpoint in secondary_networks.items():
                        try:
                            network = self.docker_client.networks.get(net_name)
                            await asyncio.to_thread(
                                network.connect, new_container, **endpoint
                            )
                        except Exception as net_err:
                            failed_networks.append(net_name)
                            logger.warning(
                                f"Failed to reconnect {container_name} to network {net_name}: {net_err}"
                            )

                logger.info(f"Recreated container: {container_name}")
                msg = f"✅ {container_name} updated (pulled {image_name} and recreated)"
                if failed_networks:
                    msg += f"\n⚠️ Failed to reconnect networks: {', '.join(failed_networks)}"
                return msg

            except Exception as recreate_err:
                # Step 5: Rollback -- try to recreate with old image
                logger.error(
                    f"Failed to recreate {container_name} with new image: {recreate_err}",
                    exc_info=True,
                )
                # Use old_image_id if available, fall back to image_name
                rollback_image = old_image_id or image_name
                try:
                    await asyncio.to_thread(
                        self.docker_client.containers.run,  # type: ignore[arg-type]
                        rollback_image,
                        name=container_name,
                        detach=True,
                        **run_config,
                    )
                    logger.info(f"Rolled back {container_name} to previous image")
                    return (
                        f"❌ Failed to recreate {container_name} with new image. "
                        f"Rolled back to previous version. Check logs for details."
                    )
                except Exception as rollback_err:
                    logger.error(
                        f"CRITICAL: Rollback also failed for {container_name}: {rollback_err}",
                        exc_info=True,
                    )
                    return (
                        f"❌ CRITICAL: {container_name} was removed but could not be recreated. "
                        f"Manual intervention required. Check logs for details."
                    )

        except docker.errors.NotFound:
            return f"❌ Container '{container_name}' not found"
        except Exception as e:
            logger.error(f"Failed to pull and recreate {container_name}: {e}", exc_info=True)
            return f"❌ Failed to update {container_name}. Check logs for details."

    @staticmethod
    def _get_secondary_networks(attrs: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Extract secondary network connections (excluding the primary NetworkMode).

        Returns a dict of network_name -> endpoint_config for networks that need
        to be reconnected after container creation (since containers.run only
        connects the primary network).
        """
        host_config = attrs.get("HostConfig", {})
        primary_network = host_config.get("NetworkMode", "")

        networks = attrs.get("NetworkSettings", {}).get("Networks", {})
        secondary = {}
        for net_name, net_config in networks.items():
            if net_name == primary_network:
                continue
            # Extract reconnection-relevant config
            endpoint = {}
            if net_config.get("IPAMConfig"):
                endpoint["IPAMConfig"] = net_config["IPAMConfig"]
            if net_config.get("Aliases"):
                endpoint["Aliases"] = net_config["Aliases"]
            if net_config.get("Links"):
                endpoint["Links"] = net_config["Links"]
            secondary[net_name] = endpoint
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

        # From HostConfig
        if host_config.get("Binds"):
            run_config["volumes"] = host_config["Binds"]
        if host_config.get("PortBindings"):
            run_config["ports"] = host_config["PortBindings"]
        if host_config.get("RestartPolicy"):
            run_config["restart_policy"] = host_config["RestartPolicy"]
        if host_config.get("NetworkMode"):
            run_config["network_mode"] = host_config["NetworkMode"]
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
