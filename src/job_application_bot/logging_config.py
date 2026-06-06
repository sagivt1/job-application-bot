"""Centralized logging setup: per-subsystem tags and severity colors.

Every module logs through ``logging.getLogger(__name__)``, which yields a dotted
path like ``job_application_bot.ai.brain``. This module maps those names to short,
human-friendly subsystem tags (``[AI]``, ``[Telegram]``, ...) and colorizes each
line by level so the bot's output is easy to scan. Colors are applied only when
writing to a real terminal; otherwise a plain, level-labeled format is used so
severity survives in redirected log files.
"""

import logging
import os
import sys

import colorama

# Logger-name prefix -> subsystem tag, most-specific first so that, e.g.,
# ``job_application_bot.integrations.notify`` matches before a broader
# ``job_application_bot`` rule would. Unmatched names (third-party ``telegram``,
# ``httpx``, ...) fall back to their own name.
_SUBSYSTEM_PREFIXES: tuple[tuple[str, str], ...] = (
    ("job_application_bot.system", "System"),
    ("job_application_bot.integrations.notify", "Notify"),
    ("job_application_bot.integrations.crm", "Airtable"),
    ("job_application_bot.integrations.intake", "Intake"),
    ("job_application_bot.ai", "AI"),
    ("job_application_bot.bot", "Telegram"),
    ("job_application_bot.pipeline", "Pipeline"),
)

# The entry module's ``__name__`` is ``__main__`` under
# ``python -m job_application_bot`` but ``job_application_bot.__main__`` under the
# ``job-application-bot`` console script; tag both as Main.
_MAIN_NAMES = frozenset({"__main__", "job_application_bot.__main__"})

# Chatty third-party loggers pinned to WARNING so their per-request INFO lines
# don't drown out our own logs (httpx in particular logs each request URL at
# INFO — which would leak the bot token in Telegram API calls). Real
# warnings/errors from these libraries still come through.
_NOISY_LOGGERS = ("httpx", "httpcore", "telegram", "apscheduler")

# Level -> ANSI color. The whole line is wrapped in this when color is on.
_LEVEL_COLORS: dict[int, str] = {
    logging.DEBUG: colorama.Style.DIM,
    logging.INFO: colorama.Fore.GREEN,
    logging.WARNING: colorama.Fore.YELLOW,
    logging.ERROR: colorama.Fore.RED,
    logging.CRITICAL: colorama.Style.BRIGHT + colorama.Fore.RED,
}


def _label_for(name: str) -> str:
    """Return the subsystem tag for a logger name.

    Args:
        name: The logger name (typically a module's ``__name__``).

    Returns:
        A short subsystem label (e.g. ``"AI"``), or the original name when no
        prefix matches.
    """
    if name in _MAIN_NAMES:
        return "Main"
    for prefix, label in _SUBSYSTEM_PREFIXES:
        if name == prefix or name.startswith(prefix + "."):
            return label
    return name


class SubsystemFormatter(logging.Formatter):
    """Format records as ``[<subsystem>]: <message>``, colored by level.

    When ``color`` is on, the line is wrapped in the level's ANSI color. When
    off, the level name is included instead (``[<subsystem>] <LEVEL>: ...``) so
    severity isn't lost in plain log files.
    """

    def __init__(self, *, color: bool) -> None:
        """Initialize the formatter.

        Args:
            color: Whether to emit ANSI color codes.
        """
        super().__init__()
        self._color = color

    def format(self, record: logging.LogRecord) -> str:
        """Render a single log record.

        Args:
            record: The record to format.

        Returns:
            The formatted (and optionally colorized) line.
        """
        label = _label_for(record.name)
        message = record.getMessage()
        if record.exc_info:
            message = f"{message}\n{self.formatException(record.exc_info)}"

        if self._color:
            color = _LEVEL_COLORS.get(record.levelno, "")
            return f"{color}[{label}]: {message}{colorama.Style.RESET_ALL}"
        return f"[{label}] {record.levelname}: {message}"


def _should_color() -> bool:
    """Decide whether log output should be colorized.

    Honors the ``NO_COLOR`` convention and only colors a real terminal.

    Returns:
        ``True`` if color codes should be emitted.
    """
    if os.environ.get("NO_COLOR"):
        return False
    return sys.stderr.isatty()


def setup_logging(level: int = logging.INFO) -> None:
    """Install the subsystem-tagged, colored formatter on the root logger.

    Replaces any existing root handlers with a single stderr ``StreamHandler``
    using :class:`SubsystemFormatter`. On Windows, enables ANSI processing via
    colorama when coloring.

    Args:
        level: The root log level (defaults to ``logging.INFO``).
    """
    color = _should_color()
    if color:
        colorama.just_fix_windows_console()

    handler = logging.StreamHandler()
    handler.setFormatter(SubsystemFormatter(color=color))

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    for name in _NOISY_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)
