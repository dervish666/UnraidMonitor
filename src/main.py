import asyncio
import logging
import os
import signal
import sys
from pathlib import Path
from typing import cast

import anthropic
import docker
from aiogram import Dispatcher

from src.config import Settings, AppConfig
from src.constants import DEFAULT_HAIKU_MODEL
from src.alerts.manager import ChatIdStore
from src.bot.telegram_bot import create_bot, create_dispatcher, register_setup_wizard
from src.bot.setup_wizard import SetupWizard
from src.background import _BackgroundTasks
from src.restart import restart_bot
from src.startup import start_monitoring


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


_shutting_down = False

async def _graceful_shutdown(dp: Dispatcher) -> None:
    """Signal handler: stop polling so the finally block runs."""
    global _shutting_down
    if _shutting_down:
        return
    _shutting_down = True
    logger.info("Received shutdown signal, stopping...")
    await dp.stop_polling()


async def main() -> None:
    config_path = os.environ.get("CONFIG_PATH", "config/config.yaml")
    first_run = not Path(config_path).exists()

    try:
        settings = Settings()  # type: ignore[call-arg]
    except Exception as e:
        logger.error(f"Failed to load settings from environment: {e}")
        sys.exit(1)

    bot = create_bot(settings.telegram_bot_token)
    chat_id_store = ChatIdStore(json_path="data/chat_ids.json")
    dp = create_dispatcher(cast(list[int], settings.telegram_allowed_users), chat_id_store=chat_id_store)

    bg = _BackgroundTasks()

    wizard_provider = None
    if settings.anthropic_api_key:
        from src.services.llm.anthropic_provider import AnthropicProvider
        _wizard_anthropic_client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        wizard_provider = AnthropicProvider(
            client=_wizard_anthropic_client, model=DEFAULT_HAIKU_MODEL
        )

    if first_run:
        logger.info("No config.yaml found -- starting setup wizard")

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
            await restart_bot(
                bot, dp, chat_id_store,
                notice="✅ Setup complete! Restarting to apply configuration...",
            )

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
        try:
            config = AppConfig(settings)
        except Exception as e:
            logger.error(f"Failed to load configuration: {e}")
            sys.exit(1)

        try:
            docker_client = docker.DockerClient(base_url=config.docker.socket_path)
        except Exception as e:
            logger.error(f"Failed to connect to Docker for setup wizard: {e}")
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
                await restart_bot(
                    bot, dp, chat_id_store,
                    notice="✅ Configuration updated! Restarting to apply changes...",
                )

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
