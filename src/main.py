import asyncio
import logging
import sys

import anthropic

from src.config import Settings, AppConfig
from src.state import ContainerStateManager
from src.monitors.docker_events import DockerEventMonitor
from src.monitors.log_watcher import LogWatcher
from src.monitors.resource_monitor import ResourceMonitor
from src.alerts.manager import AlertManager, ChatIdStore
from src.alerts.rate_limiter import RateLimiter
from src.alerts.ignore_manager import IgnoreManager
from src.alerts.recent_errors import RecentErrorsBuffer
from src.alerts.mute_manager import MuteManager
from src.alerts.server_mute_manager import ServerMuteManager
from src.bot.telegram_bot import create_bot, create_dispatcher, register_commands
from src.unraid.client import UnraidClientWrapper
from src.unraid.monitors.system_monitor import UnraidSystemMonitor


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class AlertManagerProxy:
    """Proxy that gets chat_id dynamically from ChatIdStore."""

    def __init__(self, bot, chat_id_store: ChatIdStore):
        self.bot = bot
        self.chat_id_store = chat_id_store

    async def send_crash_alert(self, **kwargs):
        chat_id = self.chat_id_store.get_chat_id()
        if chat_id:
            manager = AlertManager(self.bot, chat_id)
            await manager.send_crash_alert(**kwargs)
        else:
            logger.warning("No chat ID yet, cannot send crash alert")

    async def send_log_error_alert(self, **kwargs):
        chat_id = self.chat_id_store.get_chat_id()
        if chat_id:
            manager = AlertManager(self.bot, chat_id)
            await manager.send_log_error_alert(**kwargs)
        else:
            logger.warning("No chat ID yet, cannot send log error alert")

    async def send_resource_alert(self, **kwargs):
        chat_id = self.chat_id_store.get_chat_id()
        if chat_id:
            manager = AlertManager(self.bot, chat_id)
            await manager.send_resource_alert(**kwargs)
        else:
            logger.warning("No chat ID yet, cannot send resource alert")


async def main() -> None:
    # Load configuration
    try:
        settings = Settings()
        config = AppConfig(settings)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    logging.getLogger().setLevel(config.log_level)
    logger.info("Configuration loaded")

    # Initialize Anthropic client if API key is configured
    anthropic_client = None
    if config.anthropic_api_key:
        anthropic_client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        logger.info("Anthropic client initialized for AI diagnostics")
    else:
        logger.warning("ANTHROPIC_API_KEY not set - /diagnose command will be disabled")

    # Initialize state manager
    state = ContainerStateManager()

    # Initialize chat ID store and rate limiter
    chat_id_store = ChatIdStore()
    log_watching_config = config.log_watching
    rate_limiter = RateLimiter(cooldown_seconds=log_watching_config["cooldown_seconds"])

    # Initialize ignore manager and recent errors buffer
    ignore_manager = IgnoreManager(
        config_ignores=log_watching_config.get("container_ignores", {}),
        json_path="data/ignored_errors.json",
    )
    recent_errors_buffer = RecentErrorsBuffer(
        max_age_seconds=log_watching_config.get("cooldown_seconds", 900),
        max_per_container=50,
    )

    # Initialize mute manager
    mute_manager = MuteManager(json_path="data/mutes.json")

    # Initialize Telegram bot
    bot = create_bot(config.telegram_bot_token)
    dp = create_dispatcher(config.telegram_allowed_users, chat_id_store=chat_id_store)

    # Create alert manager proxy
    alert_manager = AlertManagerProxy(bot, chat_id_store)

    # Initialize Unraid components if configured
    unraid_client = None
    unraid_system_monitor = None
    server_mute_manager = None

    unraid_config = config.unraid
    if unraid_config.enabled and settings.unraid_api_key:
        logger.info("Initializing Unraid monitoring...")

        server_mute_manager = ServerMuteManager(json_path="data/server_mutes.json")

        unraid_client = UnraidClientWrapper(
            host=unraid_config.host,
            api_key=settings.unraid_api_key,
            port=unraid_config.port,
            verify_ssl=unraid_config.verify_ssl,
        )

        # Alert callback for Unraid
        async def on_server_alert(title: str, message: str, alert_type: str) -> None:
            chat_id = chat_id_store.get_chat_id()
            if chat_id:
                alert_text = f"SERVER ALERT: {title}\n\n{message}"
                await bot.send_message(chat_id, alert_text)
            else:
                logger.warning("No chat ID yet, cannot send server alert")

        unraid_system_monitor = UnraidSystemMonitor(
            client=unraid_client,
            config=unraid_config,
            on_alert=on_server_alert,
            mute_manager=server_mute_manager,
        )
    else:
        if not unraid_config.enabled:
            logger.info("Unraid monitoring disabled in config")
        elif not settings.unraid_api_key:
            logger.warning("UNRAID_API_KEY not set - Unraid monitoring disabled")

    # Initialize Docker monitor with alert support
    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=config.ignored_containers,
        alert_manager=alert_manager,
        rate_limiter=rate_limiter,
        mute_manager=mute_manager,
    )

    try:
        monitor.connect()
        monitor.load_initial_state()
    except Exception as e:
        logger.error(f"Failed to connect to Docker: {e}")
        sys.exit(1)

    # Initialize log watcher
    async def on_log_error(container_name: str, error_line: str):
        """Handle log errors with rate limiting."""
        # Check if muted
        if mute_manager.is_muted(container_name):
            logger.debug(f"Suppressed log error alert for muted container: {container_name}")
            return

        if rate_limiter.should_alert(container_name):
            suppressed = rate_limiter.get_suppressed_count(container_name)
            await alert_manager.send_log_error_alert(
                container_name=container_name,
                error_line=error_line,
                suppressed_count=suppressed,
            )
            rate_limiter.record_alert(container_name)
        else:
            rate_limiter.record_suppressed(container_name)

    log_watcher = LogWatcher(
        containers=log_watching_config["containers"],
        error_patterns=log_watching_config["error_patterns"],
        ignore_patterns=log_watching_config["ignore_patterns"],
        on_error=on_log_error,
        ignore_manager=ignore_manager,
        recent_errors_buffer=recent_errors_buffer,
    )

    try:
        log_watcher.connect()
    except Exception as e:
        logger.error(f"Failed to initialize log watcher: {e}")
        sys.exit(1)

    # Initialize resource monitor if enabled
    resource_monitor = None
    resource_config = config.resource_monitoring
    if resource_config.enabled:
        resource_monitor = ResourceMonitor(
            docker_client=monitor._client,
            config=resource_config,
            alert_manager=alert_manager,
            rate_limiter=rate_limiter,
            mute_manager=mute_manager,
        )
        logger.info("Resource monitoring enabled")
    else:
        logger.info("Resource monitoring disabled")

    # Register commands with docker client for /logs
    confirmation, diagnostic_service = register_commands(
        dp,
        state,
        docker_client=monitor._client,
        protected_containers=config.protected_containers,
        anthropic_client=anthropic_client,
        resource_monitor=resource_monitor,
        ignore_manager=ignore_manager,
        recent_errors_buffer=recent_errors_buffer,
        mute_manager=mute_manager,
        unraid_system_monitor=unraid_system_monitor,
        server_mute_manager=server_mute_manager,
    )

    # Start Docker event monitor as background task
    monitor_task = asyncio.create_task(monitor.start())

    # Start log watcher as background task
    log_watcher_task = asyncio.create_task(log_watcher.start())

    # Start resource monitor as background task (if enabled)
    resource_monitor_task = None
    if resource_monitor is not None:
        resource_monitor_task = asyncio.create_task(resource_monitor.start())

    # Connect to Unraid and start monitoring
    unraid_monitor_task = None
    if unraid_client:
        try:
            await unraid_client.connect()
            if unraid_system_monitor:
                unraid_monitor_task = asyncio.create_task(unraid_system_monitor.start())
                logger.info("Unraid system monitoring started")
        except Exception as e:
            logger.error(f"Failed to connect to Unraid: {e}")

    logger.info("Starting Telegram bot...")

    try:
        # Run bot until shutdown (aiogram handles SIGINT/SIGTERM)
        await dp.start_polling(bot)
    finally:
        logger.info("Shutting down...")
        monitor.stop()
        log_watcher.stop()
        if resource_monitor is not None:
            resource_monitor.stop()
        monitor_task.cancel()
        log_watcher_task.cancel()
        if resource_monitor_task is not None:
            resource_monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        try:
            await log_watcher_task
        except asyncio.CancelledError:
            pass
        if resource_monitor_task is not None:
            try:
                await resource_monitor_task
            except asyncio.CancelledError:
                pass
        if unraid_system_monitor:
            await unraid_system_monitor.stop()
        if unraid_monitor_task:
            unraid_monitor_task.cancel()
            try:
                await unraid_monitor_task
            except asyncio.CancelledError:
                pass
        if unraid_client:
            await unraid_client.disconnect()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
