import asyncio
import logging

import docker

logger = logging.getLogger(__name__)


class ContainerController:
    """Controls Docker containers with protection support."""

    def __init__(
        self,
        docker_client: docker.DockerClient,
        protected_containers: list[str],
    ):
        self.docker_client = docker_client
        self.protected_containers = set(protected_containers)

    def is_protected(self, container_name: str) -> bool:
        """Check if container is protected from control commands."""
        return container_name in self.protected_containers

    async def restart(self, container_name: str) -> str:
        """Restart a container. Returns a status message."""
        try:
            container = self.docker_client.containers.get(container_name)
            await asyncio.to_thread(container.restart)
            logger.info(f"Restarted container: {container_name}")
            return f"✅ {container_name} restarted successfully"
        except docker.errors.NotFound:
            return f"❌ Container '{container_name}' not found"
        except Exception as e:
            logger.error(f"Failed to restart {container_name}: {e}")
            return f"❌ Failed to restart {container_name}: {e}"

    async def stop(self, container_name: str) -> str:
        """Stop a container. Returns a status message."""
        try:
            container = self.docker_client.containers.get(container_name)
            if container.status != "running":
                return f"ℹ️ {container_name} is already stopped"

            await asyncio.to_thread(container.stop)
            logger.info(f"Stopped container: {container_name}")
            return f"✅ {container_name} stopped"
        except docker.errors.NotFound:
            return f"❌ Container '{container_name}' not found"
        except Exception as e:
            logger.error(f"Failed to stop {container_name}: {e}")
            return f"❌ Failed to stop {container_name}: {e}"

    async def start(self, container_name: str) -> str:
        """Start a container. Returns a status message."""
        try:
            container = self.docker_client.containers.get(container_name)
            if container.status == "running":
                return f"ℹ️ {container_name} is already running"

            await asyncio.to_thread(container.start)
            logger.info(f"Started container: {container_name}")
            return f"✅ {container_name} started"
        except docker.errors.NotFound:
            return f"❌ Container '{container_name}' not found"
        except Exception as e:
            logger.error(f"Failed to start {container_name}: {e}")
            return f"❌ Failed to start {container_name}: {e}"

    async def pull_and_recreate(self, container_name: str) -> str:
        """Pull latest image and recreate container. Returns a status message."""
        try:
            container = self.docker_client.containers.get(container_name)
            image_name = container.image.tags[0] if container.image.tags else container.image.id

            # Pull latest image
            logger.info(f"Pulling image for {container_name}: {image_name}")
            await asyncio.to_thread(self.docker_client.images.pull, image_name)

            # Get container config before stopping
            config = container.attrs

            # Stop and remove old container
            await asyncio.to_thread(container.stop)
            await asyncio.to_thread(container.remove)

            # Recreate container with same config
            new_container = await asyncio.to_thread(
                self.docker_client.containers.run,
                image_name,
                name=container_name,
                detach=True,
                **self._extract_run_config(config),
            )

            logger.info(f"Recreated container: {container_name}")
            return f"✅ {container_name} updated (pulled {image_name} and recreated)"

        except docker.errors.NotFound:
            return f"❌ Container '{container_name}' not found"
        except Exception as e:
            logger.error(f"Failed to pull and recreate {container_name}: {e}")
            return f"❌ Failed to update {container_name}: {e}"

    def _extract_run_config(self, attrs: dict) -> dict:
        """Extract run configuration from container attributes."""
        config = attrs.get("Config", {})
        host_config = attrs.get("HostConfig", {})

        run_config = {}

        if config.get("Env"):
            run_config["environment"] = config["Env"]

        if host_config.get("Binds"):
            run_config["volumes"] = host_config["Binds"]

        if host_config.get("PortBindings"):
            run_config["ports"] = host_config["PortBindings"]

        if host_config.get("RestartPolicy"):
            run_config["restart_policy"] = host_config["RestartPolicy"]

        if host_config.get("NetworkMode"):
            run_config["network_mode"] = host_config["NetworkMode"]

        return run_config
