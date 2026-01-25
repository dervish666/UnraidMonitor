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
from src.bot.telegram_bot import create_bot, create_dispatcher, register_commands


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

    # Initialize Telegram bot
    bot = create_bot(config.telegram_bot_token)
    dp = create_dispatcher(config.telegram_allowed_users, chat_id_store=chat_id_store)

    # Create alert manager proxy
    alert_manager = AlertManagerProxy(bot, chat_id_store)

    # Initialize Docker monitor with alert support
    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=config.ignored_containers,
        alert_manager=alert_manager,
        rate_limiter=rate_limiter,
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
    )

    # Start Docker event monitor as background task
    monitor_task = asyncio.create_task(monitor.start())

    # Start log watcher as background task
    log_watcher_task = asyncio.create_task(log_watcher.start())

    # Start resource monitor as background task (if enabled)
    resource_monitor_task = None
    if resource_monitor is not None:
        resource_monitor_task = asyncio.create_task(resource_monitor.start())

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
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
