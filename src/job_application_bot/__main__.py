"""Entry point: configure logging, build the Telegram Application, start polling.

Run via the console script ``job-application-bot`` or ``python -m job_application_bot``.
"""

import logging

from telegram.ext import Application, MessageHandler, filters

from job_application_bot.bot import handle_job
from job_application_bot.config import get_settings
from job_application_bot.logging_config import setup_logging

# Dedicated logger for app lifecycle events; its name maps to the [System] tag.
system_logger = logging.getLogger("job_application_bot.system")


async def _on_startup(app: Application) -> None:
    """Log a startup banner once the Application has initialized (pre-polling)."""
    system_logger.info("job-application-bot started — polling for jobs")


async def _on_shutdown(app: Application) -> None:
    """Log a clean-exit banner after the Application has shut down."""
    system_logger.info("job-application-bot stopped — shutdown complete")


def main() -> None:
    """Configure logging, build the Telegram Application, and start polling."""
    setup_logging()
    s = get_settings()
    app = (
        Application.builder()
        .token(s.telegram_bot_token)
        .post_init(_on_startup)
        .post_shutdown(_on_shutdown)
        .build()
    )
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_job))
    app.run_polling()


if __name__ == "__main__":
    main()
