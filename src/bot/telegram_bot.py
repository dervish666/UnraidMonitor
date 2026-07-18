import logging
from typing import Any, Awaitable, Callable, TYPE_CHECKING

from aiogram import Bot, Dispatcher, BaseMiddleware, F
from aiogram.filters import Command, Filter
from aiogram.types import Message, CallbackQuery, TelegramObject
import docker

from src.state import ContainerStateManager
from src.bot.commands import help_command, help_section_callback, help_back_callback, status_command, logs_command
from src.bot.control_commands import (
    restart_command,
    stop_command,
    start_command,
    pull_command,
    create_ctrl_confirm_callback,
    create_ctrl_cancel_callback,
)
from src.bot.diagnose_command import diagnose_command, diag_details_callback
from src.bot.ignore_command import (
    ignore_command,
    ignores_command,
    IgnoreSelectionState,
    ignore_similar_callback,
    ignore_toggle_callback,
    ignore_all_callback,
    ignore_done_callback,
    ignore_cancel_callback,
)
from src.bot.alert_callbacks import (
    restart_callback,
    logs_callback,
    diagnose_callback,
    mute_callback,
    pull_callback,
    raise_limit_callback,
    set_limit_callback,
    mem_kill_callback,
    mem_cancel_kill_callback,
    mem_restart_callback,
    mem_restart_yes_callback,
    mem_restart_no_callback,
    array_mute_callback,
    array_threshold_callback,
    array_set_threshold_callback,
    server_mute_callback,
    server_threshold_callback,
    server_set_threshold_callback,
)
from src.bot.memory_commands import cancel_kill_command
from src.bot.mute_command import mute_command, mutes_command, unmute_command
from src.bot.manage_command import (
    manage_command,
    manage_back_callback,
    manage_status_callback,
    manage_resources_callback,
    manage_server_callback,
    manage_disks_callback,
    manage_ignores_callback,
    manage_ignores_container_callback,
    manage_mutes_callback,
    manage_delete_ignore_callback,
    manage_delete_mute_callback,
    manage_features_callback,
    feat_image_toggle_callback,
    feat_heal_open_callback,
    feat_heal_toggle_callback,
    feat_heal_save_callback,
    feat_memres_open_callback,
    feat_memres_toggle_callback,
    feat_memres_save_callback,
    ContainerSelectionState,
)
from src.bot.resources_command import resources_command
from src.bot.unraid_commands import (
    server_command,
    mute_server_command,
    unmute_server_command,
    array_command,
    disks_command,
    mute_array_command,
    unmute_array_command,
)
from src.services.container_control import ContainerController
from src.services.diagnostic import DiagnosticService

if TYPE_CHECKING:
    from src.monitors.memory_monitor import MemoryMonitor
    from src.bot.setup_wizard import SetupWizard

logger = logging.getLogger(__name__)


class AuthMiddleware(BaseMiddleware):
    def __init__(self, allowed_users: list[int], chat_id_store: Any = None) -> None:
        self.allowed_users = set(allowed_users)
        self.chat_id_store = chat_id_store
        super().__init__()

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user_id = None
        if isinstance(event, (Message, CallbackQuery)) and event.from_user:
            user_id = event.from_user.id

        if user_id not in self.allowed_users:
            logger.warning(f"Unauthorized access attempt from user {user_id}")
            return None

        # Capture chat ID for alerts if store is provided (Messages have .chat directly)
        if self.chat_id_store is not None and isinstance(event, Message) and event.chat:
            self.chat_id_store.set_chat_id(event.chat.id)

        return await handler(event, data)


def create_auth_middleware(allowed_users: list[int], chat_id_store: Any = None) -> AuthMiddleware:
    """Factory function for auth middleware."""
    return AuthMiddleware(allowed_users, chat_id_store=chat_id_store)


def create_bot(token: str) -> Bot:
    """Create Telegram bot instance."""
    return Bot(token=token)


def create_dispatcher(allowed_users: list[int], chat_id_store: Any = None) -> Dispatcher:
    """Create dispatcher with auth middleware."""
    dp = Dispatcher()
    auth = AuthMiddleware(allowed_users, chat_id_store=chat_id_store)
    dp.message.middleware(auth)
    dp.callback_query.middleware(auth)
    return dp


def _register_ignore_commands(
    dp: Dispatcher,
    ignore_manager: Any,
    recent_errors_buffer: Any,
    pattern_analyzer: Any | None,
) -> None:
    """Register /ignore and /ignores commands with selection callbacks."""
    selection_state = IgnoreSelectionState()
    dp.message.register(
        ignore_command(recent_errors_buffer, ignore_manager, selection_state),
        Command("ignore"),
    )
    dp.message.register(ignores_command(ignore_manager), Command("ignores"))
    dp.callback_query.register(ignore_toggle_callback(selection_state), F.data.startswith("ign_toggle:"))
    dp.callback_query.register(ignore_all_callback(selection_state), F.data == "ign_all")
    dp.callback_query.register(
        ignore_done_callback(ignore_manager, selection_state, pattern_analyzer), F.data == "ign_done",
    )
    dp.callback_query.register(ignore_cancel_callback(selection_state), F.data == "ign_cancel")
    dp.callback_query.register(
        ignore_similar_callback(ignore_manager, pattern_analyzer, recent_errors_buffer),
        F.data.startswith("ignore_similar:"),
    )


def _register_unraid_commands(
    dp: Dispatcher,
    unraid_system_monitor: Any | None,
    server_mute_manager: Any | None,
    array_mute_manager: Any | None,
    array_monitor: Any | None,
    unraid_config: Any | None,
) -> None:
    """Register Unraid server/array/disk commands and alert button callbacks."""
    if unraid_system_monitor is not None:
        dp.message.register(server_command(unraid_system_monitor), Command("server"))
        dp.message.register(array_command(unraid_system_monitor), Command("array"))
        dp.message.register(disks_command(unraid_system_monitor), Command("disks"))

    if server_mute_manager is not None:
        dp.message.register(mute_server_command(server_mute_manager), Command("mute-server"))
        dp.message.register(unmute_server_command(server_mute_manager), Command("unmute-server"))
        dp.callback_query.register(server_mute_callback(server_mute_manager), F.data.startswith("srv_mute:"))

    if server_mute_manager is not None and unraid_config is not None:
        dp.callback_query.register(server_threshold_callback(unraid_config), F.data.startswith("srv_thresh:"))
        dp.callback_query.register(server_set_threshold_callback(unraid_config), F.data.startswith("srv_set:"))

    if array_mute_manager is not None:
        dp.message.register(mute_array_command(array_mute_manager), Command("mute-array"))
        dp.message.register(
            unmute_array_command(array_mute_manager, array_monitor=array_monitor), Command("unmute-array"),
        )
        dp.callback_query.register(array_mute_callback(array_mute_manager), F.data.startswith("arr_mute:"))

    if array_mute_manager is not None and unraid_config is not None:
        dp.callback_query.register(array_threshold_callback(unraid_config), F.data.startswith("arr_thresh:"))
        dp.callback_query.register(array_set_threshold_callback(unraid_config), F.data.startswith("arr_set:"))


def _register_memory_commands(
    dp: Dispatcher,
    memory_monitor: "MemoryMonitor | None",
    protected_containers: list[str] | None,
    memory_config: Any | None = None,
) -> None:
    """Register memory management commands and kill/restart button callbacks."""
    dp.message.register(cancel_kill_command(memory_monitor), Command("cancel-kill"))
    if memory_monitor is not None:
        dp.callback_query.register(
            mem_kill_callback(memory_monitor, protected_containers=protected_containers),
            F.data.startswith("mem_kill:"),
        )
        dp.callback_query.register(mem_cancel_kill_callback(memory_monitor), F.data == "mem_cancel_kill")
        # "mem_restart_yes:"/"mem_restart_no:" don't match startswith("mem_restart:")
        # (underscore vs colon after the prefix), so the three handlers are disjoint.
        if memory_config is not None:
            dp.callback_query.register(
                mem_restart_callback(memory_monitor, memory_config, protected_containers),
                F.data.startswith("mem_restart:"),
            )
        dp.callback_query.register(mem_restart_yes_callback(memory_monitor), F.data.startswith("mem_restart_yes:"))
        dp.callback_query.register(mem_restart_no_callback(memory_monitor), F.data.startswith("mem_restart_no:"))


def _register_manage_commands(
    dp: Dispatcher,
    state: ContainerStateManager,
    ignore_manager: Any,
    mute_manager: Any,
    resource_monitor: Any | None,
    unraid_system_monitor: Any | None,
    server_mute_manager: Any | None,
    array_mute_manager: Any | None,
    image_update_monitor: Any | None = None,
    image_updates_config: Any | None = None,
    auto_heal_config: Any | None = None,
    memory_config: Any | None = None,
    protected_containers: list[str] | None = None,
    restart_cb: Callable[[], Awaitable[None]] | None = None,
) -> None:
    """Register /manage dashboard command and all its sub-callbacks."""
    dp.message.register(manage_command(unraid_system_monitor), Command("manage"))
    dp.callback_query.register(manage_back_callback(unraid_system_monitor), F.data == "manage:back")
    dp.callback_query.register(manage_status_callback(state), F.data == "manage:status")
    dp.callback_query.register(manage_resources_callback(resource_monitor), F.data == "manage:resources")
    dp.callback_query.register(manage_server_callback(unraid_system_monitor), F.data == "manage:server")
    dp.callback_query.register(manage_disks_callback(unraid_system_monitor), F.data == "manage:disks")
    dp.callback_query.register(manage_ignores_callback(ignore_manager), F.data == "manage:ignores")
    dp.callback_query.register(manage_ignores_container_callback(ignore_manager), F.data.startswith("manage:ignores:"))
    dp.callback_query.register(
        manage_mutes_callback(mute_manager, server_mute_manager, array_mute_manager), F.data == "manage:mutes",
    )
    dp.callback_query.register(manage_delete_ignore_callback(ignore_manager), F.data.startswith("mdi:"))
    dp.callback_query.register(
        manage_delete_mute_callback(mute_manager, server_mute_manager, array_mute_manager),
        F.data.startswith("mdm:"),
    )

    # Features panel: image updates / auto-heal / memory restart list
    heal_selection = ContainerSelectionState()
    memres_selection = ContainerSelectionState()
    dp.callback_query.register(
        manage_features_callback(image_update_monitor, auto_heal_config, memory_config),
        F.data == "manage:features",
    )
    dp.callback_query.register(
        feat_image_toggle_callback(image_updates_config, restart_cb), F.data.startswith("feat:img:"),
    )
    dp.callback_query.register(
        feat_heal_open_callback(state, auto_heal_config, heal_selection, protected_containers),
        F.data == "feat:heal",
    )
    dp.callback_query.register(
        feat_heal_toggle_callback(state, heal_selection, protected_containers),
        F.data.startswith("fh_tog:"),
    )
    dp.callback_query.register(
        feat_heal_save_callback(auto_heal_config, heal_selection, image_update_monitor, memory_config),
        F.data == "fh_save",
    )
    dp.callback_query.register(
        feat_memres_open_callback(state, memory_config, memres_selection, protected_containers),
        F.data == "feat:memres",
    )
    dp.callback_query.register(
        feat_memres_toggle_callback(state, memres_selection, protected_containers),
        F.data.startswith("mr_tog:"),
    )
    dp.callback_query.register(
        feat_memres_save_callback(memory_config, memres_selection, image_update_monitor, auto_heal_config),
        F.data == "mr_save",
    )


def register_commands(
    dp: Dispatcher,
    state: ContainerStateManager,
    docker_client: docker.DockerClient | None = None,
    protected_containers: list[str] | None = None,
    registry: Any | None = None,
    resource_monitor: Any | None = None,
    resource_config: Any | None = None,
    ignore_manager: Any | None = None,
    recent_errors_buffer: Any | None = None,
    mute_manager: Any | None = None,
    unraid_system_monitor: Any | None = None,
    server_mute_manager: Any | None = None,
    array_mute_manager: Any | None = None,
    array_monitor: Any | None = None,
    unraid_config: Any | None = None,
    memory_monitor: "MemoryMonitor | None" = None,
    memory_config: Any | None = None,
    pattern_analyzer: Any | None = None,
    nl_processor: Any | None = None,
    ai_config: Any | None = None,
    bot_config: Any | None = None,
    image_update_monitor: Any | None = None,
    image_updates_config: Any | None = None,
    auto_heal_config: Any | None = None,
    restart_cb: Callable[[], Awaitable[None]] | None = None,
) -> tuple[ContainerController | None, DiagnosticService | None]:
    """Register all command handlers.

    Returns tuple of (ContainerController, DiagnosticService) if docker_client provided.
    """
    dp.message.register(help_command(), Command("help"))
    dp.callback_query.register(help_section_callback(), F.data.startswith("help:") & (F.data != "help:back"))
    dp.callback_query.register(help_back_callback(), F.data == "help:back")
    dp.message.register(status_command(state, resource_monitor), Command("status"))

    if docker_client:
        _log_max_lines = bot_config.log_max_lines if bot_config else 100
        _log_max_chars = bot_config.log_max_chars if bot_config else 4000
        _diagnose_max_lines = bot_config.diagnose_max_lines if bot_config else 500

        dp.message.register(
            logs_command(state, docker_client, max_lines=_log_max_lines, max_chars=_log_max_chars), Command("logs"),
        )

        controller = ContainerController(docker_client, protected_containers or [])
        dp.message.register(restart_command(state, controller), Command("restart"))
        dp.message.register(stop_command(state, controller), Command("stop"))
        dp.message.register(start_command(state, controller), Command("start"))
        dp.message.register(pull_command(state, controller), Command("pull"))
        dp.callback_query.register(create_ctrl_confirm_callback(state, controller), F.data.startswith("ctrl_confirm:"))
        dp.callback_query.register(create_ctrl_cancel_callback(), F.data == "ctrl_cancel")

        diagnostic_service = DiagnosticService(
            docker_client,
            registry=registry,
            feature="diagnostic",
            brief_max_tokens=ai_config.diagnostic_brief_max_tokens if ai_config else 300,
            detail_max_tokens=ai_config.diagnostic_detail_max_tokens if ai_config else 800,
            context_expiry_seconds=ai_config.diagnostic_context_expiry_seconds if ai_config else 600,
        )
        dp.message.register(diagnose_command(state, diagnostic_service, max_lines=_diagnose_max_lines), Command("diagnose"))
        dp.callback_query.register(diag_details_callback(diagnostic_service), F.data.startswith("diag_details:"))

        if resource_monitor is not None:
            dp.message.register(resources_command(resource_monitor), Command("resources"))

        if ignore_manager is not None and recent_errors_buffer is not None:
            _register_ignore_commands(dp, ignore_manager, recent_errors_buffer, pattern_analyzer)

        # Alert action button callbacks
        dp.callback_query.register(restart_callback(state, controller), F.data.startswith("restart:"))
        dp.callback_query.register(pull_callback(state, controller), F.data.startswith("pull:"))
        dp.callback_query.register(
            logs_callback(state, docker_client, max_lines=_log_max_lines, max_chars=_log_max_chars),
            F.data.startswith("logs:"),
        )
        dp.callback_query.register(diagnose_callback(state, diagnostic_service), F.data.startswith("diagnose:"))
        if mute_manager is not None:
            dp.callback_query.register(mute_callback(state, mute_manager), F.data.startswith("mute:"))

        if resource_config is not None:
            dp.callback_query.register(raise_limit_callback(resource_config), F.data.startswith("res_limit:"))
            dp.callback_query.register(set_limit_callback(resource_config), F.data.startswith("res_set:"))

        if mute_manager is not None:
            dp.message.register(mute_command(state, mute_manager), Command("mute"))
            dp.message.register(mutes_command(mute_manager, server_mute_manager, array_mute_manager), Command("mutes"))
            dp.message.register(unmute_command(state, mute_manager), Command("unmute"))

        _register_unraid_commands(dp, unraid_system_monitor, server_mute_manager, array_mute_manager, array_monitor, unraid_config)
        _register_memory_commands(dp, memory_monitor, protected_containers, memory_config)

        if ignore_manager is not None and mute_manager is not None:
            _register_manage_commands(
                dp, state, ignore_manager, mute_manager,
                resource_monitor, unraid_system_monitor, server_mute_manager, array_mute_manager,
                image_update_monitor=image_update_monitor,
                image_updates_config=image_updates_config,
                auto_heal_config=auto_heal_config,
                memory_config=memory_config,
                protected_containers=protected_containers,
                restart_cb=restart_cb,
            )

        if registry is not None:
            from src.bot.model_command import (
                model_command as _model_command, model_provider_callback, model_select_callback, model_back_callback,
            )
            dp.message.register(_model_command(registry), Command("model"))
            dp.callback_query.register(
                model_provider_callback(registry),
                F.data.startswith("model:") & ~F.data.startswith("model_select:") & (F.data != "model:back"),
            )
            dp.callback_query.register(model_back_callback(registry), F.data == "model:back")
            dp.callback_query.register(model_select_callback(registry), F.data.startswith("model_select:"))

        # NL handler must be last — catches all non-command text
        if nl_processor is not None and controller is not None:
            from src.bot.nl_handler import NLFilter, create_nl_handler, create_nl_confirm_callback, create_nl_cancel_callback
            dp.callback_query.register(create_nl_confirm_callback(nl_processor, controller), F.data.startswith("nl_confirm:"))
            dp.callback_query.register(create_nl_cancel_callback(nl_processor), F.data == "nl_cancel")
            dp.message.register(create_nl_handler(nl_processor), NLFilter())

        return controller, diagnostic_service

    return None, None


# ---------------------------------------------------------------------------
# Setup wizard registration
# ---------------------------------------------------------------------------

def register_setup_wizard(
    dp: Dispatcher,
    wizard: "SetupWizard",
    on_complete: Callable[[], Awaitable[None]] | None = None,
    register_start: bool = True,
) -> None:
    """Register setup wizard handlers on the dispatcher.

    This adds the setup mode middleware (blocks non-wizard commands during
    setup) and registers all wizard-related message/callback handlers.

    Args:
        register_start: If True, also register /start to trigger the wizard.
            Set to False on normal runs to avoid conflicting with the
            container /start command registered by register_commands.
    """
    from src.bot.setup_wizard import (
        SetupModeMiddleware,
        WizardState,
        create_start_handler,
        create_cancel_handler,
        create_host_handler,
        create_confirm_callback,
        create_toggle_callback,
        create_adjust_callback,
        create_adjust_done_callback,
    )

    # Middleware blocks non-wizard commands while setup is active
    dp.message.middleware(SetupModeMiddleware(wizard))

    # /start triggers the wizard only on first run; /setup always available
    if register_start:
        dp.message.register(create_start_handler(wizard), Command("start"))
    dp.message.register(create_start_handler(wizard), Command("setup"))
    dp.message.register(create_cancel_handler(wizard), Command("cancel"))

    # Custom filter: only match text messages when wizard is awaiting host
    class AwaitingHostFilter(Filter):
        async def __call__(self, message: Message) -> bool:
            user_id = message.from_user.id if message.from_user else 0
            return wizard.get_state(user_id) == WizardState.AWAITING_HOST

    dp.message.register(create_host_handler(wizard), AwaitingHostFilter())

    # Callback queries for wizard buttons
    dp.callback_query.register(
        create_confirm_callback(wizard, on_complete=on_complete),
        F.data == "setup:confirm",
    )
    dp.callback_query.register(
        create_adjust_callback(wizard),
        F.data.startswith("setup:adjust:"),
    )
    dp.callback_query.register(
        create_toggle_callback(wizard),
        F.data.startswith("setup:toggle:"),
    )
    dp.callback_query.register(
        create_adjust_done_callback(wizard),
        F.data == "setup:adjust_done",
    )
