import asyncio
import logging
import signal
import sys

from src.config import Settings
from src.state import ContainerStateManager
from src.monitors.docker_events import DockerEventMonitor
from src.bot.telegram_bot import create_bot, create_dispatcher, register_commands


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


async def main() -> None:
    # Load configuration
    try:
        settings = Settings()
    except Exception as e:
        logger.error(f"Failed to load configuration: {e}")
        sys.exit(1)

    logging.getLogger().setLevel(settings.log_level)
    logger.info("Configuration loaded")

    # Initialize state manager
    state = ContainerStateManager()

    # Initialize Docker monitor
    monitor = DockerEventMonitor(
        state_manager=state,
        ignored_containers=[],  # TODO: load from config
    )

    try:
        monitor.connect()
        monitor.load_initial_state()
    except Exception as e:
        logger.error(f"Failed to connect to Docker: {e}")
        sys.exit(1)

    # Initialize Telegram bot
    bot = create_bot(settings.telegram_bot_token)
    dp = create_dispatcher(settings.telegram_allowed_users)
    register_commands(dp, state)

    # Handle shutdown signals
    shutdown_event = asyncio.Event()

    def signal_handler(sig: int, frame) -> None:
        logger.info(f"Received signal {sig}, shutting down...")
        shutdown_event.set()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # Start Docker event monitor as background task
    monitor_task = asyncio.create_task(monitor.start())

    logger.info("Starting Telegram bot...")

    try:
        # Run bot until shutdown
        await dp.start_polling(bot, handle_signals=False)
    finally:
        logger.info("Shutting down...")
        monitor.stop()
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
        await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
