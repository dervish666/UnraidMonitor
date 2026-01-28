import asyncio
import logging
import os
import sys

import anthropic

from src.config import Settings, AppConfig, generate_default_config
from src.state import ContainerStateManager
from src.monitors.docker_events import DockerEventMonitor
from src.monitors.log_watcher import LogWatcher
from src.monitors.memory_monitor import MemoryMonitor
from src.monitors.resource_monitor import ResourceMonitor
from src.alerts.manager import AlertManager, ChatIdStore
from src.alerts.rate_limiter import RateLimiter
from src.alerts.ignore_manager import IgnoreManager
from src.alerts.recent_errors import RecentErrorsBuffer
from src.alerts.mute_manager import MuteManager
from src.alerts.server_mute_manager import ServerMuteManager
from src.alerts.array_mute_manager import ArrayMuteManager
from src.bot.telegram_bot import create_bot, create_dispatcher, register_commands
from src.analysis.pattern_analyzer import PatternAnalyzer
from src.unraid.client import UnraidClientWrapper
from src.unraid.monitors.system_monitor import UnraidSystemMonitor
from src.unraid.monitors.array_monitor import ArrayMonitor


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


class AlertManagerProxy:
    """Proxy that gets chat_id dynamically from ChatIdStore."""

    def __init__(self, bot, chat_id_store: ChatIdStore, error_display_max_chars: int = 200):
        self.bot = bot
        self.chat_id_store = chat_id_store
        self.error_display_max_chars = error_display_max_chars

    async def _send_alert(self, method_name: str, **kwargs):
        """Generic alert sender that delegates to AlertManager."""
        chat_id = self.chat_id_store.get_chat_id()
        if chat_id:
            manager = AlertManager(self.bot, chat_id, error_display_max_chars=self.error_display_max_chars)
            await getattr(manager, method_name)(**kwargs)
        else:
            logger.warning(f"No chat ID yet, cannot send {method_name.replace('_', ' ')}")

    async def send_crash_alert(self, **kwargs):
        await self._send_alert("send_crash_alert", **kwargs)

    async def send_log_error_alert(self, **kwargs):
        await self._send_alert("send_log_error_alert", **kwargs)

    async def send_resource_alert(self, **kwargs):
        await self._send_alert("send_resource_alert", **kwargs)


async def main() -> None:
    # Check for first run and generate default config if needed
    config_path = os.environ.get("CONFIG_PATH", "config/config.yaml")
    first_run = generate_default_config(config_path)
    if first_run:
        logger.info(f"Created default config at {config_path}")

    # Load configuration
    try:
        settings = Settings()
        config = AppConfig(settings)
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    logging.getLogger().setLevel(config.log_level)
    logger.info("Configuration loaded")

    # Load sub-configs
    ai_config = config.ai
    bot_config = config.bot
    docker_config = config.docker

    # Initialize Anthropic client if API key is configured
    anthropic_client = None
    pattern_analyzer = None
    if config.anthropic_api_key:
        anthropic_client = anthropic.Anthropic(api_key=config.anthropic_api_key)
        pattern_analyzer = PatternAnalyzer(
            anthropic_client,
            model=ai_config.pattern_analyzer_model,
            max_tokens=ai_config.pattern_analyzer_max_tokens,
            context_lines=ai_config.pattern_analyzer_context_lines,
        )
        logger.info("Anthropic client initialized for AI diagnostics and pattern analysis")
    else:
        logger.warning("ANTHROPIC_API_KEY not set - /diagnose and smart ignore patterns will be disabled")

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
    )

    # Initialize mute manager
    mute_manager = MuteManager(json_path="data/mutes.json")

    # Initialize Telegram bot
    bot = create_bot(config.telegram_bot_token)
    dp = create_dispatcher(config.telegram_allowed_users, chat_id_store=chat_id_store)

    # Create alert manager proxy
    alert_manager = AlertManagerProxy(bot, chat_id_store, error_display_max_chars=bot_config.error_display_max_chars)

    # Initialize Unraid components if configured
    unraid_client = None
    unraid_system_monitor = None
    unraid_array_monitor = None
    server_mute_manager = None
    array_mute_manager = None

    unraid_config = config.unraid
    if unraid_config.enabled and settings.unraid_api_key:
        logger.info("Initializing Unraid monitoring...")

        server_mute_manager = ServerMuteManager(json_path="data/server_mutes.json")
        array_mute_manager = ArrayMuteManager(json_path="data/array_mutes.json")

        unraid_client = UnraidClientWrapper(
            host=unraid_config.host,
            api_key=settings.unraid_api_key,
            port=unraid_config.port,
            verify_ssl=unraid_config.verify_ssl,
            use_ssl=unraid_config.use_ssl,
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

        unraid_array_monitor = ArrayMonitor(
            client=unraid_client,
            config=unraid_config,
            on_alert=on_server_alert,
            mute_manager=array_mute_manager,
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
        docker_socket_path=docker_config.socket_path,
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
        docker_socket_path=docker_config.socket_path,
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

    # Initialize memory monitor if enabled
    memory_monitor = None
    memory_config = config.memory_management
    if memory_config.enabled:
        async def on_memory_alert(title: str, message: str) -> None:
            chat_id = chat_id_store.get_chat_id()
            if chat_id:
                alert_text = f"{'🔴' if 'Critical' in title else '⚠️'} *{title}*\n\n{message}"
                await bot.send_message(chat_id, alert_text, parse_mode="Markdown")

        async def on_ask_restart(container: str) -> None:
            chat_id = chat_id_store.get_chat_id()
            if chat_id:
                text = f"💾 Memory now at safe levels. Restart {container}?"
                # TODO: Add inline keyboard with Yes/No buttons
                await bot.send_message(chat_id, text)

        memory_monitor = MemoryMonitor(
            docker_client=monitor._client,
            config=memory_config,
            on_alert=on_memory_alert,
            on_ask_restart=on_ask_restart,
        )
        logger.info("Memory monitoring enabled")

    # Create NL processor if enabled
    nl_processor = None
    if anthropic_client and monitor._client:
        from src.services.nl_processor import NLProcessor
        from src.services.nl_tools import NLToolExecutor

        nl_executor = NLToolExecutor(
            state=state,
            docker_client=monitor._client,
            protected_containers=config.protected_containers,
            controller=None,  # Will be set after register_commands
            resource_monitor=resource_monitor,
            recent_errors_buffer=recent_errors_buffer,
            unraid_system_monitor=unraid_system_monitor,
            log_max_chars=bot_config.nl_log_max_chars,
        )
        nl_processor = NLProcessor(
            anthropic_client=anthropic_client,
            tool_executor=nl_executor,
            model=ai_config.nl_processor_model,
            max_tokens=ai_config.nl_processor_max_tokens,
            max_tool_iterations=ai_config.nl_max_tool_iterations,
            max_conversation_exchanges=ai_config.nl_max_conversation_exchanges,
        )

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
        array_mute_manager=array_mute_manager,
        memory_monitor=memory_monitor,
        pattern_analyzer=pattern_analyzer,
        nl_processor=nl_processor,
        ai_config=ai_config,
        bot_config=bot_config,
    )

    # Set controller on NL executor after register_commands creates it
    if nl_processor and confirmation:
        # Create controller for NL executor
        from src.services.container_control import ContainerController
        nl_controller = ContainerController(monitor._client, config.protected_containers)
        nl_processor._executor._controller = nl_controller

    # Start Docker event monitor as background task
    monitor_task = asyncio.create_task(monitor.start())

    # Start log watcher as background task
    log_watcher_task = asyncio.create_task(log_watcher.start())

    # Start resource monitor as background task (if enabled)
    resource_monitor_task = None
    if resource_monitor is not None:
        resource_monitor_task = asyncio.create_task(resource_monitor.start())

    # Start memory monitor as background task (if enabled)
    memory_monitor_task = None
    if memory_monitor is not None:
        memory_monitor_task = asyncio.create_task(memory_monitor.start())

    # Connect to Unraid and start monitoring
    unraid_monitor_task = None
    unraid_array_monitor_task = None
    if unraid_client:
        try:
            await unraid_client.connect()
            if unraid_system_monitor:
                unraid_monitor_task = asyncio.create_task(unraid_system_monitor.start())
                logger.info("Unraid system monitoring started")
            if unraid_array_monitor:
                unraid_array_monitor_task = asyncio.create_task(unraid_array_monitor.start())
                logger.info("Unraid array monitoring started")
        except Exception as e:
            logger.error(f"Failed to connect to Unraid: {e}")

    logger.info("Starting Telegram bot...")

    # Send first-run welcome message if needed
    if first_run:
        async def send_welcome():
            await asyncio.sleep(5)  # Wait for chat_id to be available
            chat_id = chat_id_store.get_chat_id()
            if chat_id:
                await bot.send_message(
                    chat_id,
                    "👋 *First run!* Default config created.\n\n"
                    "Edit `/app/config/config.yaml` to customize settings.\n"
                    "Use /help to get started.",
                    parse_mode="Markdown",
                )

        asyncio.create_task(send_welcome())

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
        if memory_monitor is not None:
            memory_monitor.stop()
        if memory_monitor_task is not None:
            memory_monitor_task.cancel()
            try:
                await memory_monitor_task
            except asyncio.CancelledError:
                pass
        if unraid_system_monitor:
            await unraid_system_monitor.stop()
        if unraid_array_monitor:
            await unraid_array_monitor.stop()
        if unraid_monitor_task:
            unraid_monitor_task.cancel()
            try:
                await unraid_monitor_task
            except asyncio.CancelledError:
                pass
        if unraid_array_monitor_task:
            unraid_array_monitor_task.cancel()
            try:
                await unraid_array_monitor_task
            except asyncio.CancelledError:
                pass
        if unraid_client:
            await unraid_client.disconnect()
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
