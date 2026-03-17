import asyncio
import logging
import os
import signal
import sys
from pathlib import Path

import anthropic
import docker

from src.config import Settings, AppConfig
from src.state import ContainerStateManager
from src.monitors.docker_events import DockerEventMonitor
from src.monitors.log_watcher import LogWatcher
from src.monitors.memory_monitor import MemoryMonitor
from src.monitors.resource_monitor import ResourceMonitor
from src.alerts.manager import AlertManager, ChatIdStore
from src.alerts.rate_limiter import RateLimiter
from src.utils.formatting import escape_markdown
from src.utils.telegram_retry import send_with_retry
from src.alerts.ignore_manager import IgnoreManager
from src.alerts.recent_errors import RecentErrorsBuffer
from src.alerts.mute_manager import MuteManager
from src.alerts.server_mute_manager import ServerMuteManager
from src.alerts.array_mute_manager import ArrayMuteManager
from src.bot.telegram_bot import create_bot, create_dispatcher, register_commands, register_setup_wizard
from src.bot.setup_wizard import SetupWizard
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
    """Proxy that gets chat_id dynamically from ChatIdStore.

    Queues alerts if no chat ID is available yet, delivering them
    when the first /start command provides the chat ID.
    Includes a simple send lock to prevent Telegram rate limit abuse
    during alert storms.
    """

    MAX_QUEUED = 50
    # Minimum delay between consecutive alert sends (seconds)
    _SEND_DELAY = 0.1

    def __init__(self, bot, chat_id_store: ChatIdStore, error_display_max_chars: int = 200):
        self.bot = bot
        self.chat_id_store = chat_id_store
        self.error_display_max_chars = error_display_max_chars
        self._queued_alerts: list[tuple[str, dict]] = []
        self._managers: dict[int, AlertManager] = {}
        self._send_lock = asyncio.Lock()

    def _get_manager(self, chat_id: int) -> AlertManager:
        """Get or create a cached AlertManager for the given chat_id."""
        if chat_id not in self._managers:
            self._managers[chat_id] = AlertManager(
                self.bot, chat_id, error_display_max_chars=self.error_display_max_chars
            )
        return self._managers[chat_id]

    async def _send_alert(self, method_name: str, **kwargs):
        """Generic alert sender that delegates to AlertManager.

        Sends to all known chat IDs. Uses a lock to serialize sends
        and a small delay between them, preventing Telegram rate limit
        abuse during alert storms.
        """
        chat_ids = self.chat_id_store.get_all_chat_ids()
        if chat_ids:
            async with self._send_lock:
                # Flush any queued alerts first
                if self._queued_alerts:
                    await self._flush_queue(chat_ids)
                for chat_id in chat_ids:
                    try:
                        manager = self._get_manager(chat_id)
                        await getattr(manager, method_name)(**kwargs)
                    except Exception as e:
                        logger.error(f"Failed to send alert to {chat_id}: {e}")
                await asyncio.sleep(self._SEND_DELAY)
        else:
            if len(self._queued_alerts) < self.MAX_QUEUED:
                self._queued_alerts.append((method_name, kwargs))
                logger.info(f"Queued {method_name.replace('_', ' ')} (no chat ID yet, {len(self._queued_alerts)} queued)")
            else:
                logger.warning(f"Alert queue full, dropping {method_name.replace('_', ' ')}")

    async def _flush_queue(self, chat_ids: set[int]) -> None:
        """Deliver all queued alerts to all chat IDs."""
        queued = self._queued_alerts[:]
        self._queued_alerts.clear()
        logger.info(f"Flushing {len(queued)} queued alerts to {len(chat_ids)} users")
        for method_name, kwargs in queued:
            for chat_id in chat_ids:
                try:
                    manager = self._get_manager(chat_id)
                    await getattr(manager, method_name)(**kwargs)
                except Exception as e:
                    logger.error(f"Failed to send queued alert to {chat_id}: {e}")

    async def send_crash_alert(self, **kwargs):
        await self._send_alert("send_crash_alert", **kwargs)

    async def send_log_error_alert(self, **kwargs):
        await self._send_alert("send_log_error_alert", **kwargs)

    async def send_resource_alert(self, **kwargs):
        await self._send_alert("send_resource_alert", **kwargs)

    async def send_recovery_alert(self, container_name: str):
        await self._send_alert("send_recovery_alert", container_name=container_name)

    async def send_health_alert(self, **kwargs):
        await self._send_alert("send_health_alert", **kwargs)


# ---------------------------------------------------------------------------
# Background task tracker -- used by start_monitoring and shutdown
# ---------------------------------------------------------------------------

class _BackgroundTasks:
    """Holds references to all background tasks and stoppable components."""

    def __init__(self) -> None:
        self.monitor: DockerEventMonitor | None = None
        self.log_watcher: LogWatcher | None = None
        self.resource_monitor: ResourceMonitor | None = None
        self.memory_monitor: MemoryMonitor | None = None
        self.unraid_client: UnraidClientWrapper | None = None
        self.unraid_system_monitor: UnraidSystemMonitor | None = None
        self.unraid_array_monitor: ArrayMonitor | None = None
        self.mute_managers: list = []
        self._tasks: list[asyncio.Task] = []

    def add_task(self, task: asyncio.Task) -> None:
        self._tasks.append(task)

    async def shutdown(self) -> None:
        """Stop all monitors, flush state, and cancel all tasks."""
        if self.monitor:
            await self.monitor.stop_async()
        if self.log_watcher:
            self.log_watcher.stop()
        if self.resource_monitor is not None:
            self.resource_monitor.stop()
        if self.memory_monitor is not None:
            self.memory_monitor.stop()
        if self.unraid_system_monitor:
            await self.unraid_system_monitor.stop()
        if self.unraid_array_monitor:
            await self.unraid_array_monitor.stop()
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        if self.unraid_client:
            await self.unraid_client.disconnect()
        # Flush any deferred mute state to disk
        for mgr in self.mute_managers:
            try:
                mgr.flush()
            except Exception as e:
                logger.error(f"Failed to flush mute manager: {e}")


# ---------------------------------------------------------------------------
# Monitor startup -- extracted so the wizard on_complete can call it
# ---------------------------------------------------------------------------

async def start_monitoring(
    config: AppConfig,
    settings: Settings,
    bot,
    dp,
    chat_id_store: ChatIdStore,
    bg: _BackgroundTasks,
) -> None:
    """Start all monitors and register bot commands.

    This is called either directly on normal runs or by the wizard's
    on_complete callback after the first-run setup finishes.
    """
    logging.getLogger().setLevel(config.log_level)
    logger.info("Configuration loaded -- starting monitors")

    # Load sub-configs
    ai_config = config.ai
    bot_config = config.bot
    docker_config = config.docker

    from src.services.llm.registry import ProviderRegistry
    from src.services.llm.ollama_provider import OllamaProvider
    import openai as openai_sdk

    # Build provider registry from available API keys
    anthropic_client = None
    openai_client = None
    ollama_client = None
    ollama_models = []

    if config.anthropic_api_key:
        anthropic_client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

    if settings.openai_api_key:
        openai_client = openai_sdk.AsyncOpenAI(api_key=settings.openai_api_key)

    if config.ai.ollama_host:
        ollama_client = openai_sdk.AsyncOpenAI(
            base_url=f"{config.ai.ollama_host}/v1",
            api_key="ollama",
        )
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                ollama_models = await OllamaProvider.discover_models(
                    host=config.ai.ollama_host, session=session,
                )
        except Exception as e:
            logger.warning(f"Failed to discover Ollama models: {e}")

    # Filter out None values from feature_models
    feature_models_raw = {
        "nl_processor": ai_config.nl_processor_model,
        "diagnostic": ai_config.diagnostic_model,
        "pattern_analyzer": ai_config.pattern_analyzer_model,
    }
    feature_models = {k: v for k, v in feature_models_raw.items() if v is not None}

    registry = ProviderRegistry(
        anthropic_client=anthropic_client,
        openai_client=openai_client,
        ollama_client=ollama_client,
        ollama_models=ollama_models,
        default_model=ai_config.default_model,
        feature_models=feature_models,
        ollama_default_model=ai_config.ollama_default_model,
    )

    # Log which providers are available
    providers = registry.get_available_providers()
    if providers:
        provider_names = ", ".join(p.display_name for p in providers)
        logger.info(f"LLM providers available: {provider_names}")
    else:
        logger.warning("No LLM providers configured - AI features will be disabled")

    # Create pattern analyzer using registry
    pattern_analyzer_provider = registry.get_provider("pattern_analyzer")
    pattern_analyzer = PatternAnalyzer(
        provider=pattern_analyzer_provider,
        max_tokens=ai_config.pattern_analyzer_max_tokens,
        context_lines=ai_config.pattern_analyzer_context_lines,
    ) if pattern_analyzer_provider else None

    # Initialize state manager
    state = ContainerStateManager()

    # Initialize rate limiter and alert helpers
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

        # Alert callback for Unraid -- captures resource_monitor via closure
        # (resource_monitor is defined later in this function)
        resource_monitor_ref: list[ResourceMonitor | None] = [None]

        async def on_server_alert(title: str, message: str, alert_type: str) -> None:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

            chat_ids = chat_id_store.get_all_chat_ids()
            if not chat_ids:
                logger.warning("No chat ID yet, cannot send server alert")
                return

            safe_title = escape_markdown(title)
            alert_text = f"🖥️ *SERVER ALERT:* {safe_title}\n\n{message}"
            keyboard = None

            # Enhance memory alerts with per-container stats and kill buttons
            if title == "Memory Critical" and resource_monitor_ref[0] is not None:
                try:
                    all_stats = await asyncio.wait_for(
                        resource_monitor_ref[0].get_all_stats(), timeout=5.0
                    )
                    # Sort by memory usage descending, take top 5
                    all_stats.sort(key=lambda s: s.memory_bytes, reverse=True)
                    top = all_stats[:5]

                    if top:
                        top_text = ", ".join(f"{s.name} ({s.memory_display})" for s in top)
                        alert_text += f"\n\nTop memory: {top_text}"

                        protected = set(config.protected_containers or [])
                        buttons = []
                        for s in top:
                            if s.name not in protected:
                                label = f"⏹ Stop {s.name} ({s.memory_display})"
                                buttons.append(
                                    [InlineKeyboardButton(
                                        text=label, callback_data=f"mem_kill:{s.name}"
                                    )]
                                )
                        if buttons:
                            keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)
                except Exception as e:
                    logger.warning(f"Failed to get container stats for server alert: {e}")

            for cid in chat_ids:
                try:
                    await send_with_retry(
                        bot.send_message, chat_id=cid, text=alert_text, parse_mode="Markdown", reply_markup=keyboard
                    )
                except Exception as e:
                    logger.error(f"Failed to send server alert to {cid}: {e}")

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
        await asyncio.to_thread(monitor.load_initial_state)
    except Exception as e:
        logger.error(f"Failed to connect to Docker: {e}")
        raise

    bg.monitor = monitor

    # Initialize log watcher
    async def on_log_error(container_name: str, error_line: str):
        """Handle log errors with rate limiting."""
        # Check if muted
        if mute_manager.is_muted(container_name):
            logger.debug(f"Suppressed log error alert for muted container: {container_name}")
            return

        if rate_limiter.should_alert(container_name):
            suppressed = rate_limiter.get_suppressed_count(container_name)
            # Record alert BEFORE the await to prevent TOCTOU duplicate alerts
            rate_limiter.record_alert(container_name)
            await alert_manager.send_log_error_alert(
                container_name=container_name,
                error_line=error_line,
                suppressed_count=suppressed,
            )
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
        raise

    bg.log_watcher = log_watcher

    # Initialize resource monitor if enabled
    resource_monitor = None
    resource_config = config.resource_monitoring
    if resource_config.enabled:
        resource_monitor = ResourceMonitor(
            docker_client=monitor.shared_client,
            config=resource_config,
            alert_manager=alert_manager,
            rate_limiter=rate_limiter,
            mute_manager=mute_manager,
        )
        logger.info("Resource monitoring enabled")
    else:
        logger.info("Resource monitoring disabled")

    bg.resource_monitor = resource_monitor

    # Backfill the resource_monitor reference for server alert closure
    if unraid_client:
        resource_monitor_ref[0] = resource_monitor

    # Initialize memory monitor if enabled
    memory_monitor = None
    memory_config = config.memory_management
    if memory_config.enabled:
        async def on_memory_alert(
            title: str, message: str, alert_type: str, killable_names: list[str]
        ) -> None:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

            chat_ids = chat_id_store.get_all_chat_ids()
            if not chat_ids:
                return

            emoji = "🔴" if "Critical" in title else "⚠️"
            alert_text = f"{emoji} *{escape_markdown(title)}*\n\n{message}"
            keyboard = None

            if alert_type in ("warning", "critical") and killable_names:
                # Try to get per-container memory stats for button labels
                stats_by_name: dict[str, str] = {}
                if resource_monitor is not None:
                    try:
                        all_stats = await asyncio.wait_for(
                            resource_monitor.get_all_stats(), timeout=5.0
                        )
                        stats_by_name = {s.name: s.memory_display for s in all_stats}
                    except Exception:
                        pass  # Graceful degradation — buttons without memory info

                if alert_type == "warning":
                    buttons = []
                    for name in killable_names:
                        mem = stats_by_name.get(name, "")
                        label = f"⏹ Stop {name}" + (f" ({mem})" if mem else "")
                        buttons.append(
                            [InlineKeyboardButton(text=label, callback_data=f"mem_kill:{name}")]
                        )
                    if buttons:
                        keyboard = InlineKeyboardMarkup(inline_keyboard=buttons)

                elif alert_type == "critical":
                    target = killable_names[0]
                    mem = stats_by_name.get(target, "")
                    kill_label = f"⏹ Kill {target} Now" + (f" ({mem})" if mem else "")
                    keyboard = InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text=kill_label, callback_data=f"mem_kill:{target}")],
                        [InlineKeyboardButton(text="❌ Cancel Auto-Kill", callback_data="mem_cancel_kill")],
                    ])

            for cid in chat_ids:
                try:
                    await send_with_retry(
                        bot.send_message,
                        chat_id=cid, text=alert_text, parse_mode="Markdown", reply_markup=keyboard,
                    )
                except Exception as e:
                    logger.error(f"Failed to send memory alert to {cid}: {e}")

        async def on_ask_restart(container: str) -> None:
            from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

            safe_name = escape_markdown(container)
            for cid in chat_id_store.get_all_chat_ids():
                text = f"💾 Memory now at safe levels. Restart *{safe_name}*?"
                keyboard = InlineKeyboardMarkup(inline_keyboard=[
                    [
                        InlineKeyboardButton(text="✅ Yes", callback_data=f"mem_restart_yes:{container}"),
                        InlineKeyboardButton(text="❌ No", callback_data=f"mem_restart_no:{container}"),
                    ]
                ])
                try:
                    await send_with_retry(
                        bot.send_message, chat_id=cid, text=text, parse_mode="Markdown", reply_markup=keyboard
                    )
                except Exception as e:
                    logger.error(f"Failed to send restart prompt to {cid}: {e}")

        memory_monitor = MemoryMonitor(
            docker_client=monitor.shared_client,
            config=memory_config,
            on_alert=on_memory_alert,
            on_ask_restart=on_ask_restart,
        )
        logger.info("Memory monitoring enabled")

    bg.memory_monitor = memory_monitor

    # Create NL processor if enabled
    nl_processor = None
    nl_provider = registry.get_provider("nl_processor")
    if nl_provider and monitor.shared_client:
        from src.services.nl_processor import NLProcessor
        from src.services.nl_tools import NLToolExecutor

        nl_executor = NLToolExecutor(
            state=state,
            docker_client=monitor.shared_client,
            protected_containers=config.protected_containers,
            controller=None,  # Will be set after register_commands
            resource_monitor=resource_monitor,
            recent_errors_buffer=recent_errors_buffer,
            unraid_system_monitor=unraid_system_monitor,
            log_max_chars=bot_config.nl_log_max_chars,
        )
        nl_processor = NLProcessor(
            provider=nl_provider,
            tool_executor=nl_executor,
            max_tokens=ai_config.nl_processor_max_tokens,
            max_tool_iterations=ai_config.nl_max_tool_iterations,
            max_conversation_exchanges=ai_config.nl_max_conversation_exchanges,
        )

    # Register commands with docker client for /logs
    controller, diagnostic_service = register_commands(
        dp,
        state,
        docker_client=monitor.shared_client,
        protected_containers=config.protected_containers,
        registry=registry,
        resource_monitor=resource_monitor,
        ignore_manager=ignore_manager,
        recent_errors_buffer=recent_errors_buffer,
        mute_manager=mute_manager,
        unraid_system_monitor=unraid_system_monitor,
        server_mute_manager=server_mute_manager,
        array_mute_manager=array_mute_manager,
        array_monitor=unraid_array_monitor,
        memory_monitor=memory_monitor,
        pattern_analyzer=pattern_analyzer,
        nl_processor=nl_processor,
        ai_config=ai_config,
        bot_config=bot_config,
    )

    # Set controller on NL executor after register_commands creates it
    if nl_processor and controller:
        nl_processor._executor._controller = controller

    # Store Unraid references for shutdown
    bg.unraid_client = unraid_client
    bg.unraid_system_monitor = unraid_system_monitor
    bg.unraid_array_monitor = unraid_array_monitor

    # Track mute managers for flush on shutdown
    bg.mute_managers = [mute_manager]
    if server_mute_manager:
        bg.mute_managers.append(server_mute_manager)
    if array_mute_manager:
        bg.mute_managers.append(array_mute_manager)

    # Register /health command with references to all monitors
    from aiogram.filters import Command as AiogramCommand
    from src.bot.health_command import health_command
    from datetime import datetime as _dt, timezone as _tz

    _start_time = _dt.now(_tz.utc)
    dp.message.register(
        health_command(
            start_time=_start_time,
            monitor=monitor,
            log_watcher=log_watcher,
            resource_monitor=resource_monitor,
            memory_monitor=memory_monitor,
            unraid_client=unraid_client,
            unraid_system_monitor=unraid_system_monitor,
            unraid_array_monitor=unraid_array_monitor,
            alert_manager=alert_manager,
        ),
        AiogramCommand("health"),
    )

    # Start Docker event monitor as background task
    bg.add_task(asyncio.create_task(monitor.start()))

    # Start log watcher as background task
    bg.add_task(asyncio.create_task(log_watcher.start()))

    # Start resource monitor as background task (if enabled)
    if resource_monitor is not None:
        bg.add_task(asyncio.create_task(resource_monitor.start()))

    # Start memory monitor as background task (if enabled)
    if memory_monitor is not None:
        bg.add_task(asyncio.create_task(memory_monitor.start()))

    # Connect to Unraid and start monitoring
    if unraid_client:
        try:
            await unraid_client.connect()
            if unraid_system_monitor:
                bg.add_task(asyncio.create_task(unraid_system_monitor.start()))
                logger.info("Unraid system monitoring started")
            if unraid_array_monitor:
                bg.add_task(asyncio.create_task(unraid_array_monitor.start()))
                logger.info("Unraid array monitoring started")
        except Exception as e:
            logger.error(f"Failed to connect to Unraid: {e}")
            # Notify user about Unraid connection failure
            for cid in chat_id_store.get_all_chat_ids():
                try:
                    await send_with_retry(
                        bot.send_message,
                        chat_id=cid,
                        text=f"⚠️ Failed to connect to Unraid server: {e}\n"
                             f"Server monitoring is disabled. Check UNRAID_API_KEY and host settings.",
                    )
                except Exception as send_err:
                    logger.error(f"Failed to send Unraid error to {cid}: {send_err}")

    # Periodic task: flush dirty mute state & send expiry notifications
    async def _mute_maintenance_loop() -> None:
        while True:
            await asyncio.sleep(300)  # Every 5 minutes
            for mgr in bg.mute_managers:
                try:
                    mgr.flush()
                    expired = mgr.drain_expired()
                    if expired and alert_manager:
                        for key in expired:
                            for cid in chat_id_store.get_all_chat_ids():
                                try:
                                    await send_with_retry(
                                        bot.send_message,
                                        chat_id=cid,
                                        text=f"🔔 Mute expired for *{escape_markdown(key)}*",
                                        parse_mode="Markdown",
                                    )
                                except Exception as e:
                                    logger.error(f"Failed to send mute expiry notification: {e}")
                except Exception as e:
                    logger.error(f"Mute maintenance error: {e}")

    bg.add_task(asyncio.create_task(_mute_maintenance_loop()))

    logger.info("All monitors started")

    # H6: Send startup notification
    for cid in chat_id_store.get_all_chat_ids():
        container_count = len(state.get_all())
        watched_count = len(log_watching_config.get("containers", []))
        unraid_status = "connected" if (unraid_client and unraid_client.is_connected) else "disabled"
        startup_msg = (
            f"🟢 *Bot started*\n"
            f"Tracking {container_count} containers, watching logs for {watched_count}\n"
            f"Unraid: {unraid_status}"
        )
        try:
            await send_with_retry(
                bot.send_message, chat_id=cid, text=startup_msg, parse_mode="Markdown"
            )
        except Exception as e:
            logger.warning(f"Failed to send startup notification: {e}")


# ---------------------------------------------------------------------------
# Graceful shutdown helper
# ---------------------------------------------------------------------------

_shutting_down = False

async def _graceful_shutdown(dp: object) -> None:
    """Signal handler: stop polling so the finally block runs."""
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True
    logger.info("Received shutdown signal, stopping...")
    dp.stop_polling()


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

async def main() -> None:
    config_path = os.environ.get("CONFIG_PATH", "config/config.yaml")
    first_run = not Path(config_path).exists()

    # Settings always loads from env vars (no config.yaml needed)
    try:
        settings = Settings()
    except Exception as e:
        logger.error(f"Failed to load settings from environment: {e}")
        sys.exit(1)

    # Create Telegram bot and dispatcher (needed for both paths)
    bot = create_bot(settings.telegram_bot_token)
    chat_id_store = ChatIdStore()
    dp = create_dispatcher(settings.telegram_allowed_users, chat_id_store=chat_id_store)

    bg = _BackgroundTasks()

    # Optionally create Anthropic client for the wizard (from env vars)
    wizard_provider = None
    if settings.anthropic_api_key:
        from src.services.llm.anthropic_provider import AnthropicProvider
        _wizard_anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        wizard_provider = AnthropicProvider(
            client=_wizard_anthropic_client, model="claude-haiku-4-5-20251001"
        )

    if first_run:
        # ---------------------------------------------------------------
        # First run: launch wizard, defer all monitoring until it completes
        # ---------------------------------------------------------------
        logger.info("No config.yaml found -- starting setup wizard")

        # Connect a Docker client for the wizard's container listing
        try:
            docker_client = docker.DockerClient(base_url="unix:///var/run/docker.sock")
        except Exception as e:
            logger.error(f"Failed to connect to Docker for setup wizard: {e}")
            sys.exit(1)

        wizard = SetupWizard(
            config_path=config_path,
            docker_client=docker_client,
            anthropic_client=wizard_provider,
            unraid_api_key=settings.unraid_api_key,
        )

        async def on_wizard_complete() -> None:
            """Called when the wizard saves config.yaml for the first time."""
            logger.info("Setup wizard complete -- restarting to apply config")
            for cid in chat_id_store.get_all_chat_ids():
                try:
                    await bot.send_message(
                        cid,
                        "✅ Setup complete! Restarting to apply configuration...",
                    )
                except Exception:
                    pass
            # Stop polling gracefully before re-exec to flush pending updates
            dp.stop_polling()
            await asyncio.sleep(1)
            # Re-exec the process so it boots with the new config.yaml
            os.execv(sys.executable, [sys.executable, "-m", "src.main"])

        register_setup_wizard(dp, wizard, on_complete=on_wizard_complete)

        logger.info("Starting Telegram bot (setup wizard mode)...")
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(_graceful_shutdown(dp)))
        try:
            await dp.start_polling(bot)
        finally:
            await bg.shutdown()
            await bot.session.close()
    else:
        # ---------------------------------------------------------------
        # Normal run: config.yaml exists, start everything immediately
        # ---------------------------------------------------------------
        try:
            config = AppConfig(settings)
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            sys.exit(1)

        # Also register the wizard for /setup re-runs
        try:
            docker_client = docker.DockerClient(base_url=config.docker.socket_path)
        except Exception as e:
            logger.error(f"Failed to connect to Docker for setup wizard: {e}")
            # Non-fatal for the wizard; proceed without it
            docker_client = None

        if docker_client is not None:
            wizard = SetupWizard(
                config_path=config_path,
                docker_client=docker_client,
                anthropic_client=wizard_provider,
                unraid_api_key=settings.unraid_api_key,
            )

            async def on_rerun_complete() -> None:
                """Called when a /setup re-run saves updated config."""
                logger.info("Setup wizard re-run complete -- restarting to apply config")
                for cid in chat_id_store.get_all_chat_ids():
                    try:
                        await bot.send_message(
                            cid,
                            "✅ Configuration updated! Restarting to apply changes...",
                        )
                    except Exception:
                        pass
                # Stop polling gracefully before re-exec to flush pending updates
                dp.stop_polling()
                await asyncio.sleep(1)
                os.execv(sys.executable, [sys.executable, "-m", "src.main"])

            register_setup_wizard(dp, wizard, on_complete=on_rerun_complete, register_start=False)

        async def _start_monitors_safe() -> None:
            try:
                await start_monitoring(config, settings, bot, dp, chat_id_store, bg)
            except Exception as e:
                logger.error(f"Monitor startup failed: {e} -- bot still running, use /setup to reconfigure")
                for cid in chat_id_store.get_all_chat_ids():
                    try:
                        await bot.send_message(cid, f"⚠️ Monitor startup failed: {e}\nBot is still responsive.")
                    except Exception:
                        pass

        bg.add_task(asyncio.create_task(_start_monitors_safe()))

        logger.info("Starting Telegram bot...")
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, lambda: asyncio.create_task(_graceful_shutdown(dp)))
        try:
            await dp.start_polling(bot)
        finally:
            await bg.shutdown()
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
