from __future__ import annotations

import asyncio
import logging
from typing import Any

import anthropic
from aiogram import Bot, Dispatcher

from src.config import Settings, AppConfig, AIConfig
from src.state import ContainerStateManager
from src.monitors.docker_events import DockerEventMonitor
from src.monitors.log_watcher import LogWatcher
from src.monitors.memory_monitor import MemoryMonitor
from src.monitors.resource_monitor import ResourceMonitor
from src.alerts.manager import ChatIdStore
from src.alerts.rate_limiter import RateLimiter
from src.utils.formatting import escape_markdown
from src.utils.telegram_retry import send_with_retry
from src.alerts.ignore_manager import IgnoreManager
from src.alerts.recent_errors import RecentErrorsBuffer
from src.alerts.mute_manager import MuteManager
from src.alerts.server_mute_manager import ServerMuteManager
from src.alerts.array_mute_manager import ArrayMuteManager
from src.bot.telegram_bot import register_commands
from src.analysis.pattern_analyzer import PatternAnalyzer
from src.unraid.client import UnraidClientWrapper
from src.unraid.monitors.system_monitor import UnraidSystemMonitor
from src.unraid.monitors.array_monitor import ArrayMonitor
from src.alert_proxy import AlertManagerProxy
from src.background import _BackgroundTasks
from src.monitor_callbacks import (
    make_log_error_handler,
    make_server_alert_handler,
    make_memory_alert_handler,
    make_ask_restart_handler,
    make_mute_maintenance_loop,
)


logger = logging.getLogger(__name__)


class _UnraidComponents:
    __slots__ = (
        "client", "system_monitor", "array_monitor",
        "server_mute_manager", "array_mute_manager",
        "resource_monitor_ref",
    )

    def __init__(self) -> None:
        self.client: UnraidClientWrapper | None = None
        self.system_monitor: UnraidSystemMonitor | None = None
        self.array_monitor: ArrayMonitor | None = None
        self.server_mute_manager: ServerMuteManager | None = None
        self.array_mute_manager: ArrayMuteManager | None = None
        self.resource_monitor_ref: list[ResourceMonitor | None] = [None]


async def _build_provider_registry(
    config: AppConfig,
    settings: Settings,
    ai_config: AIConfig,
) -> Any:
    from src.services.llm.registry import ProviderRegistry
    from src.services.llm.ollama_provider import OllamaProvider
    import openai as openai_sdk

    anthropic_client = None
    openai_client = None
    ollama_client = None
    ollama_models: list[Any] = []

    if config.anthropic_api_key:
        anthropic_client = anthropic.AsyncAnthropic(api_key=config.anthropic_api_key)

    if settings.openai_api_key:
        openai_client = openai_sdk.AsyncOpenAI(api_key=settings.openai_api_key)

    if ai_config.ollama_host:
        ollama_client = openai_sdk.AsyncOpenAI(
            base_url=f"{ai_config.ollama_host}/v1",
            api_key="ollama",
        )
        try:
            import aiohttp
            async with aiohttp.ClientSession() as session:
                ollama_models = await OllamaProvider.discover_models(
                    host=ai_config.ollama_host, session=session,
                )
        except Exception as e:
            logger.warning(f"Failed to discover Ollama models: {e}")

    # Discover available Anthropic models so family names resolve to the latest
    discovered_anthropic: list[str] = []
    if anthropic_client is not None:
        try:
            models_page = await anthropic_client.models.list(limit=100)
            discovered_anthropic = [m.id for m in models_page.data]
            logger.info("Discovered %d Anthropic models", len(discovered_anthropic))
        except Exception as e:
            logger.info("Could not list Anthropic models (using defaults): %s", e)

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
        config_path=settings.config_path,
        ollama_default_model=ai_config.ollama_default_model,
        discovered_anthropic_models=discovered_anthropic or None,
    )

    providers = registry.get_available_providers()
    if providers:
        provider_names = ", ".join(p.display_name for p in providers)
        logger.info(f"LLM providers available: {provider_names}")
    else:
        logger.warning("No LLM providers configured - AI features will be disabled")

    return registry


def _init_unraid(
    config: AppConfig,
    settings: Settings,
    chat_id_store: ChatIdStore,
    bot: Bot,
) -> _UnraidComponents:
    uc = _UnraidComponents()
    unraid_config = config.unraid

    if unraid_config.enabled and settings.unraid_api_key:
        logger.info("Initializing Unraid monitoring...")

        uc.server_mute_manager = ServerMuteManager(json_path="data/server_mutes.json")
        uc.array_mute_manager = ArrayMuteManager(json_path="data/array_mutes.json")

        uc.client = UnraidClientWrapper(
            host=unraid_config.host,
            api_key=settings.unraid_api_key,
            port=unraid_config.port,
            verify_ssl=unraid_config.verify_ssl,
            use_ssl=unraid_config.use_ssl,
        )

        on_server_alert = make_server_alert_handler(
            chat_id_store, bot, config, escape_markdown, uc.resource_monitor_ref,
            array_mute_manager=uc.array_mute_manager,
            server_mute_manager=uc.server_mute_manager,
            unraid_config=config.unraid,
        )

        uc.system_monitor = UnraidSystemMonitor(
            client=uc.client,
            config=unraid_config,
            on_alert=on_server_alert,
            mute_manager=uc.server_mute_manager,
        )

        uc.array_monitor = ArrayMonitor(
            client=uc.client,
            config=unraid_config,
            on_alert=on_server_alert,
            mute_manager=uc.array_mute_manager,
        )
    else:
        if not unraid_config.enabled:
            logger.info("Unraid monitoring disabled in config")
        elif not settings.unraid_api_key:
            logger.warning("UNRAID_API_KEY not set - Unraid monitoring disabled")

    return uc


async def _start_background_monitors(
    bg: _BackgroundTasks,
    resource_monitor: ResourceMonitor | None,
    uc: _UnraidComponents,
    chat_id_store: ChatIdStore,
    bot: Bot,
) -> None:
    bg.add_task(asyncio.create_task(bg.monitor.start()))  # type: ignore[union-attr]
    bg.add_task(asyncio.create_task(bg.log_watcher.start()))  # type: ignore[union-attr]

    if resource_monitor is not None:
        bg.add_task(asyncio.create_task(resource_monitor.start()))

    if bg.memory_monitor is not None:
        bg.add_task(asyncio.create_task(bg.memory_monitor.start()))

    if uc.client:
        try:
            await uc.client.connect()
            if uc.system_monitor:
                bg.add_task(asyncio.create_task(uc.system_monitor.start()))
                logger.info("Unraid system monitoring started")
            if uc.array_monitor:
                bg.add_task(asyncio.create_task(uc.array_monitor.start()))
                logger.info("Unraid array monitoring started")
        except Exception as e:
            logger.error(f"Failed to connect to Unraid: {e}")
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


async def start_monitoring(
    config: AppConfig,
    settings: Settings,
    bot: Bot,
    dp: Dispatcher,
    chat_id_store: ChatIdStore,
    bg: _BackgroundTasks,
) -> None:
    """Start all monitors and register bot commands."""
    logging.getLogger().setLevel(config.log_level)
    logger.info("Configuration loaded -- starting monitors")

    ai_config = config.ai
    bot_config = config.bot
    docker_config = config.docker

    registry = await _build_provider_registry(config, settings, ai_config)

    pattern_analyzer_provider = registry.get_provider("pattern_analyzer")
    pattern_analyzer = PatternAnalyzer(
        provider=pattern_analyzer_provider,
        max_tokens=ai_config.pattern_analyzer_max_tokens,
        context_lines=ai_config.pattern_analyzer_context_lines,
    ) if pattern_analyzer_provider else None

    state = ContainerStateManager()

    log_watching_config = config.log_watching
    rate_limiter = RateLimiter(cooldown_seconds=log_watching_config["cooldown_seconds"])
    ignore_manager = IgnoreManager(
        config_ignores=log_watching_config.get("container_ignores", {}),
        json_path="data/ignored_errors.json",
    )
    recent_errors_buffer = RecentErrorsBuffer(
        max_age_seconds=log_watching_config.get("cooldown_seconds", 900),
    )
    mute_manager = MuteManager(json_path="data/mutes.json")
    alert_manager = AlertManagerProxy(bot, chat_id_store, error_display_max_chars=bot_config.error_display_max_chars)

    uc = _init_unraid(config, settings, chat_id_store, bot)

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

    on_log_error = make_log_error_handler(mute_manager, rate_limiter, alert_manager)
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

    resource_monitor = None
    resource_config = config.resource_monitoring
    if resource_config.enabled:
        resource_monitor = ResourceMonitor(
            docker_client=monitor.shared_client,  # type: ignore[arg-type]
            config=resource_config,
            alert_manager=alert_manager,
            rate_limiter=rate_limiter,
            mute_manager=mute_manager,
        )
        logger.info("Resource monitoring enabled")
    else:
        logger.info("Resource monitoring disabled")

    bg.resource_monitor = resource_monitor

    if uc.client:
        uc.resource_monitor_ref[0] = resource_monitor

    memory_monitor = None
    memory_config = config.memory_management
    if memory_config.enabled:
        on_memory_alert = make_memory_alert_handler(
            chat_id_store, bot, resource_monitor, escape_markdown,
        )
        on_ask_restart = make_ask_restart_handler(
            chat_id_store, bot, escape_markdown,
        )
        memory_monitor = MemoryMonitor(
            docker_client=monitor.shared_client,  # type: ignore[arg-type]
            config=memory_config,
            on_alert=on_memory_alert,
            on_ask_restart=on_ask_restart,
        )
        logger.info("Memory monitoring enabled")

    bg.memory_monitor = memory_monitor

    nl_processor = None
    has_nl_provider = registry.get_provider("nl_processor") is not None
    if has_nl_provider and monitor.shared_client:
        from src.services.nl_processor import NLProcessor
        from src.services.nl_tools import NLToolExecutor

        nl_executor = NLToolExecutor(
            state=state,
            docker_client=monitor.shared_client,  # type: ignore[arg-type]
            protected_containers=config.protected_containers,
            controller=None,
            resource_monitor=resource_monitor,
            recent_errors_buffer=recent_errors_buffer,
            unraid_system_monitor=uc.system_monitor,
            log_max_chars=bot_config.nl_log_max_chars,
        )
        nl_processor = NLProcessor(
            registry=registry,
            feature="nl_processor",
            tool_executor=nl_executor,
            max_tokens=ai_config.nl_processor_max_tokens,
            max_tool_iterations=ai_config.nl_max_tool_iterations,
            max_conversation_exchanges=ai_config.nl_max_conversation_exchanges,
        )

    controller, diagnostic_service = register_commands(
        dp,
        state,
        docker_client=monitor.shared_client,  # type: ignore[arg-type]
        protected_containers=config.protected_containers,
        registry=registry,
        resource_monitor=resource_monitor,
        resource_config=resource_config,
        ignore_manager=ignore_manager,
        recent_errors_buffer=recent_errors_buffer,
        mute_manager=mute_manager,
        unraid_system_monitor=uc.system_monitor,
        server_mute_manager=uc.server_mute_manager,
        array_mute_manager=uc.array_mute_manager,
        array_monitor=uc.array_monitor,
        unraid_config=config.unraid if uc.array_mute_manager else None,
        memory_monitor=memory_monitor,
        pattern_analyzer=pattern_analyzer,
        nl_processor=nl_processor,
        ai_config=ai_config,
        bot_config=bot_config,
    )

    if nl_processor and controller:
        nl_processor._executor._controller = controller

    bg.unraid_client = uc.client
    bg.unraid_system_monitor = uc.system_monitor
    bg.unraid_array_monitor = uc.array_monitor

    bg.mute_managers = [mute_manager]
    if uc.server_mute_manager:
        bg.mute_managers.append(uc.server_mute_manager)
    if uc.array_mute_manager:
        bg.mute_managers.append(uc.array_mute_manager)

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
            unraid_client=uc.client,
            unraid_system_monitor=uc.system_monitor,
            unraid_array_monitor=uc.array_monitor,
            alert_manager=alert_manager,
        ),
        AiogramCommand("health"),
    )

    await _start_background_monitors(bg, resource_monitor, uc, chat_id_store, bot)

    _mute_maintenance_loop = make_mute_maintenance_loop(
        bg.mute_managers, alert_manager, chat_id_store, bot, escape_markdown,
    )
    bg.add_task(asyncio.create_task(_mute_maintenance_loop()))

    logger.info("All monitors started")

    for cid in chat_id_store.get_all_chat_ids():
        container_count = len(state.get_all())
        watched_count = len(log_watching_config.get("containers", []))
        unraid_status = "connected" if (uc.client and uc.client.is_connected) else "disabled"
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
