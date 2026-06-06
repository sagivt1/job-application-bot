"""Day 1 throwaway integration check for job-application-bot.

Proves each external integration (Gemini, Telegram, Airtable, URL->text) works
independently using the credentials in ``.env``. This is a manual, real-credential
smoke test — it is *not* part of the real pipeline and should be deleted once the
actual modules exist.

Run with::

    uv run python scripts/check_integrations.py
"""

import os
import sys

import httpx
import trafilatura
from colorama import Fore, Style, init
from dotenv import load_dotenv
from google import genai
from pyairtable import Api

# --- Edit these ------------------------------------------------------------
# The Gemini model is read from the GEMINI_MODEL env var (see .env / .env.example).
# REPLACE this with a real job-posting URL before running the URL->text check.
JOB_URL = "https://example.com/jobs/REPLACE-WITH-A-REAL-JOB-POSTING-URL"
TELEGRAM_TEXT = "job-application-bot check_integrations.py: integration test ok"
# ---------------------------------------------------------------------------

# Result statuses. WARN means "informational, not a hard failure".
PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"

_STATUS_COLOR = {PASS: Fore.GREEN, WARN: Fore.YELLOW, FAIL: Fore.RED}
_BANNER_WIDTH = 64

# Reconfigure stdout to UTF-8 (Windows consoles often default to cp1252) before
# colorama wraps it, then enable autoreset so each print resets its own colors.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")
init(autoreset=True)
load_dotenv()


def require_env(name: str) -> str:
    """Return the value of an environment variable, or raise if it is unset.

    Args:
        name: The environment variable to read.

    Returns:
        The variable's non-empty value.

    Raises:
        RuntimeError: If the variable is missing or empty.
    """
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing env var {name}")
    return value


def banner(title: str) -> None:
    """Print a framed section header.

    Args:
        title: The text to display inside the banner.
    """
    bar = "=" * _BANNER_WIDTH
    print(f"\n{Fore.CYAN}{Style.BRIGHT}{bar}")
    print(f"{Fore.CYAN}{Style.BRIGHT}  {title}")
    print(f"{Fore.CYAN}{Style.BRIGHT}{bar}")


def report(name: str, status: str, detail: str = "") -> str:
    """Print one aligned, color-coded result line and return its status.

    Args:
        name: The integration being checked (e.g. ``"Gemini"``).
        status: One of :data:`PASS`, :data:`WARN`, or :data:`FAIL`.
        detail: Optional extra context shown dimmed after the name.

    Returns:
        The ``status`` argument, unchanged, so callers can ``return report(...)``.
    """
    color = _STATUS_COLOR[status]
    label = f"{color}{Style.BRIGHT}[ {status} ]{Style.RESET_ALL}"
    line = f"  {label}  {Style.BRIGHT}{name:<11}{Style.RESET_ALL}"
    if detail:
        line += f"{Style.DIM}{detail}{Style.RESET_ALL}"
    print(line)
    return status


def note(text: str) -> None:
    """Print an indented, dimmed follow-up note under a result line.

    Args:
        text: The note to display.
    """
    print(f"        {Style.DIM}{Fore.WHITE}-> {text}{Style.RESET_ALL}")


def check_gemini() -> str:
    """Send a trivial prompt to Gemini and confirm a text reply comes back."""
    try:
        client = genai.Client(api_key=require_env("GEMINI_API_KEY"))
        model = require_env("GEMINI_MODEL")
        resp = client.models.generate_content(
            model=model, contents="Reply with just: ok"
        )
        text = (resp.text or "").strip()
        if not text:
            return report("Gemini", FAIL, "empty response text")
        return report("Gemini", PASS, f"model={model}, reply={text!r}")
    except Exception as exc:
        return report("Gemini", FAIL, str(exc))


def check_telegram() -> str:
    """Send a test message to CHAT_ID and confirm the Bot API returns ``ok``."""
    try:
        token = require_env("TELEGRAM_BOT_TOKEN")
        chat_id = require_env("CHAT_ID")
        resp = httpx.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": TELEGRAM_TEXT},
            timeout=30,
        )
        data = resp.json()
        if not data.get("ok"):
            return report("Telegram", FAIL, str(data.get("description", data)))
        status = report("Telegram", PASS, "API returned ok")
        note("confirm the test message actually arrived in Telegram")
        return status
    except Exception as exc:
        return report("Telegram", FAIL, str(exc))


def check_airtable() -> str:
    """Create, read back, then delete one test record so the table stays empty."""
    rec_id = None
    try:
        table = Api(require_env("AIRTABLE_TOKEN")).table(
            require_env("AIRTABLE_BASE"), require_env("AIRTABLE_TABLE")
        )
        rec_id = table.create({"Company": "__verify__", "MatchScore": 1})["id"]
        fetched = table.get(rec_id)
        if fetched["fields"].get("Company") != "__verify__":
            return report("Airtable", FAIL, "read-back did not match created record")
        table.delete(rec_id)
        rec_id = None
        return report("Airtable", PASS, "create + read + delete")
    except Exception as exc:
        status = report("Airtable", FAIL, str(exc))
        # Best-effort cleanup so a partial failure doesn't leave a stray record.
        if rec_id is not None:
            try:
                table.delete(rec_id)
                note("cleaned up leftover test record")
            except Exception as cleanup_exc:
                note(f"WARNING: leftover test record {rec_id}: {cleanup_exc}")
        return status


def check_url_to_text() -> str:
    """Fetch JOB_URL and extract clean text; empty extraction is a WARN, not FAIL."""
    try:
        resp = httpx.get(
            JOB_URL,
            headers={"User-Agent": "Mozilla/5.0 (job-application-bot verify)"},
            follow_redirects=True,
            timeout=30,
        )
        resp.raise_for_status()
        text = trafilatura.extract(resp.text) or ""
        if not text.strip():
            status = report("URL->text", WARN, "extraction returned empty text")
            note("this page needs the manual-paste fallback (not a hard failure)")
            return status
        preview = " ".join(text.split())[:80]
        return report("URL->text", PASS, f"{len(text)} chars, preview={preview!r}")
    except Exception as exc:
        return report("URL->text", FAIL, str(exc))


def main() -> None:
    """Run every integration check and print a color-coded summary."""
    banner("job-application-bot - Day 1 integration checks")
    results = {
        "Gemini": check_gemini(),
        "Telegram": check_telegram(),
        "Airtable": check_airtable(),
        "URL->text": check_url_to_text(),
    }

    banner("Summary")
    for name, status in results.items():
        report(name, status)

    failed = [name for name, status in results.items() if status == FAIL]
    print()
    if failed:
        joined = ", ".join(failed)
        print(f"  {Fore.RED}{Style.BRIGHT}Overall: {len(failed)} FAILED -> {joined}")
    else:
        print(f"  {Fore.GREEN}{Style.BRIGHT}Overall: all integrations OK")


if __name__ == "__main__":
    main()
