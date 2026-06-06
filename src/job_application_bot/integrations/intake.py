"""Turn a job URL or raw pasted text into clean job text.

This is the front door of the pipeline (CLAUDE.md step 1). The Telegram handler
passes whatever the user sent to :func:`extract_job_text`, which returns an
:class:`IntakeResult` describing what to do next.

A **link is always required** — it's how the user knows where to apply. Because
many job sites block scraping (LinkedIn/Indeed), the user can paste the job text
*and* its URL in one message; intake then uses the pasted text directly and
keeps the URL. A message with no URL is rejected so the handler can ask for one.
"""

import html
import logging
import re
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

import httpx
import trafilatura

logger = logging.getLogger(__name__)

# A browser-like User-Agent avoids the 403s many job sites return to
# header-less HTTP clients.
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# Same browser-like UA but asking for JSON — used by the ATS adapters (Comeet,
# Workday) when calling a careers JSON API directly. Workday in particular 403s
# header-less clients and may serve HTML unless JSON is explicitly requested.
_JSON_HEADERS = {**_HEADERS, "Accept": "application/json"}

# Real job postings run to thousands of characters; anything shorter than this is
# almost always a login wall, cookie banner, or error stub rather than a posting.
_MIN_JOB_TEXT_CHARS = 400

# Final-URL fragments that mean the fetch was bounced to an auth/login wall
# instead of the posting. LinkedIn/Indeed redirect header-only clients here, so a
# 200 OK still yields only "Sign in…" text. Matched case-insensitively.
_AUTH_WALL_MARKERS = (
    "/login",
    "/uas/login",
    "/authwall",
    "/checkpoint",
    "/signin",
    "/sign-in",
)


def _is_auth_wall(final_url: str) -> bool:
    """Return whether *final_url* (after redirects) is a login / auth-wall page.

    Args:
        final_url: The URL httpx actually landed on after following redirects.

    Returns:
        ``True`` if the URL path looks like a sign-in / auth wall, meaning the
        real posting was not reached.
    """
    lowered = final_url.lower()
    return any(marker in lowered for marker in _AUTH_WALL_MARKERS)


def _html_to_text(fragment: str) -> str:
    """Flatten an HTML fragment into readable plain text.

    Strips tags, decodes entities (e.g. ``&nbsp;``), and collapses runs of
    whitespace into single spaces. Shared by the ATS adapters whose JSON APIs
    return JD bodies as HTML fragments.

    Args:
        fragment: An HTML string (may be empty/None-ish — falsy input yields "").

    Returns:
        The tag-free, entity-decoded, whitespace-collapsed text.
    """
    text = re.sub(r"<[^>]+>", " ", fragment or "")
    return re.sub(r"\s+", " ", html.unescape(text)).strip()


class IntakeStatus(Enum):
    """Outcome of an intake attempt, telling the handler what to do next."""

    OK = "ok"
    NEEDS_PASTE = "needs_paste"  # URL fetched but yielded no extractable text
    NEEDS_LINK = "needs_link"  # raw text supplied without any URL


@dataclass
class IntakeResult:
    """The result of turning a user message into job text.

    Attributes:
        status: What happened — see :class:`IntakeStatus`.
        text: Clean job text when ``status`` is ``OK``, otherwise ``None``.
        link: The source URL. Always set except on ``NEEDS_LINK``.
    """

    status: IntakeStatus
    text: str | None
    link: str | None


# --- Comeet adapter --------------------------------------------------------
# Comeet career pages (comeet.com / comeet.co) are a client-side Angular app: a
# bare fetch returns only the unrendered template ("{{ position.name }}"), never
# the JD, so the generic trafilatura path silently captures placeholder chrome.
# But the page embeds the company's API token, and Comeet exposes each posting
# over a public JSON API — so we can fetch the real description with plain httpx.
_COMEET_HOSTS = ("comeet.com", "comeet.co")

# The token lives in the page's COMPANY_DATA blob: ``"token": "38A11B2A..."``.
_COMEET_TOKEN_RE = re.compile(r'"token"\s*:\s*"([^"]+)"')

# Posting URLs look like /jobs/{slug}/{company_uid}/{role-slug}/{position_uid}.
_COMEET_PATH_RE = re.compile(r"/jobs/[^/]+/([^/]+)/[^/]+/([^/?#]+)")

# Public per-position endpoint; ``details=true`` includes the JD body sections.
_COMEET_API = (
    "https://www.comeet.com/careers-api/2.0/company/{company_uid}"
    "/positions/{position_uid}?token={token}&details=true"
)


def _is_comeet_url(url: str) -> bool:
    """Return whether *url* points at a Comeet-hosted careers page.

    Args:
        url: The candidate URL.

    Returns:
        ``True`` if the host is ``comeet.com``/``comeet.co`` (or a subdomain).
    """
    host = urlparse(url).netloc.lower()
    return any(host == h or host.endswith("." + h) for h in _COMEET_HOSTS)


def _comeet_details_to_text(payload: dict) -> str:
    """Flatten a Comeet position payload into clean, plain JD text.

    Joins the position name with each ``details`` section (``name`` + tag-stripped
    ``value``), ordered by the section ``order`` field.

    Args:
        payload: The JSON object returned by the Comeet careers API for one
            position (requested with ``details=true``).

    Returns:
        The job description as plain text, with HTML tags/entities removed.
    """
    parts: list[str] = []
    name = payload.get("name")
    if name:
        parts.append(str(name))

    sections = payload.get("details") or []
    for section in sorted(sections, key=lambda s: s.get("order", 0)):
        title = (section.get("name") or "").strip()
        # Section values are HTML fragments — strip tags, decode entities
        # (e.g. &nbsp;), and collapse whitespace into readable plain text.
        value = _html_to_text(section.get("value"))
        block = f"{title}\n{value}".strip()
        if block:
            parts.append(block)

    return "\n\n".join(parts).strip()


def _fetch_comeet(url: str) -> IntakeResult:
    """Resolve a Comeet posting via its public careers API.

    Parses the company/position UIDs from the URL, scrapes the page once for the
    API token, then fetches the structured JD from the careers API. Any failure
    (unexpected URL shape, missing token, HTTP/JSON error, too-short result)
    degrades to ``NEEDS_PASTE`` so the user can paste the description manually.

    Args:
        url: A Comeet posting URL.

    Returns:
        An :class:`IntakeResult` — ``OK`` with the JD text on success, otherwise
        ``NEEDS_PASTE`` (always keeping the original *url* as the link).
    """
    match = _COMEET_PATH_RE.search(urlparse(url).path)
    if match is None:
        logger.warning("intake: comeet URL has unexpected path: %s", url)
        return IntakeResult(IntakeStatus.NEEDS_PASTE, text=None, link=url)
    company_uid, position_uid = match.groups()

    try:
        page = httpx.get(url, follow_redirects=True, timeout=30.0, headers=_HEADERS)
        page.raise_for_status()
        token_match = _COMEET_TOKEN_RE.search(page.text)
        if token_match is None:
            logger.warning("intake: comeet token not found on %s", url)
            return IntakeResult(IntakeStatus.NEEDS_PASTE, text=None, link=url)

        api_url = _COMEET_API.format(
            company_uid=company_uid,
            position_uid=position_uid,
            token=token_match.group(1),
        )
        resp = httpx.get(api_url, timeout=30.0, headers=_HEADERS)
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # ValueError covers a non-JSON body (httpx .json() raises JSONDecodeError).
        logger.warning("intake: comeet API failed for %s: %s", url, exc)
        return IntakeResult(IntakeStatus.NEEDS_PASTE, text=None, link=url)

    if not isinstance(payload, dict):
        logger.warning("intake: comeet API returned unexpected shape for %s", url)
        return IntakeResult(IntakeStatus.NEEDS_PASTE, text=None, link=url)

    text = _comeet_details_to_text(payload)
    if len(text) < _MIN_JOB_TEXT_CHARS:
        logger.warning(
            "intake: comeet API yielded only %d chars for %s", len(text), url
        )
        return IntakeResult(IntakeStatus.NEEDS_PASTE, text=None, link=url)

    logger.info("intake: comeet API extracted %d chars from %s", len(text), url)
    return IntakeResult(IntakeStatus.OK, text=text, link=url)


# --- Workday adapter -------------------------------------------------------
# Workday career sites (*.myworkdayjobs.com) are JS-rendered SPAs, so a bare
# fetch returns an empty app shell rather than the JD. But every posting is
# served over Workday's public CXS (Candidate Experience Service) JSON API,
# addressable straight from the posting URL — no token scrape needed (unlike
# Comeet). The posting URL looks like:
#   https://{tenant}.{dc}.myworkdayjobs.com/{locale?}/{site}/job/{externalPath}
# and the CXS endpoint is:
#   https://{tenant}.{dc}.myworkdayjobs.com/wday/cxs/{tenant}/{site}/job/{externalPath}
_WORKDAY_HOST = "myworkdayjobs.com"


def _is_workday_url(url: str) -> bool:
    """Return whether *url* points at a Workday-hosted careers page.

    Args:
        url: The candidate URL.

    Returns:
        ``True`` if the host is (a subdomain of) ``myworkdayjobs.com``.
    """
    host = urlparse(url).netloc.lower()
    return host == _WORKDAY_HOST or host.endswith("." + _WORKDAY_HOST)


def _workday_cxs_url(url: str) -> str | None:
    """Build the CXS JSON API URL for a Workday posting URL.

    The CXS path mirrors the posting path but is prefixed with
    ``/wday/cxs/{tenant}/`` where ``tenant`` is the first subdomain label. The
    ``site`` is the path segment immediately before ``/job/`` and
    ``externalPath`` is everything after it; any leading locale segment (e.g.
    ``/en-US``) is naturally dropped because only the ``site`` + ``job`` tail is
    kept.

    Args:
        url: A Workday posting URL.

    Returns:
        The CXS API URL, or ``None`` if the path has no ``job`` segment with a
        ``site`` before it and an ``externalPath`` after it.
    """
    parsed = urlparse(url)
    tenant = parsed.netloc.split(".")[0]
    segments = [seg for seg in parsed.path.split("/") if seg]

    try:
        job_idx = segments.index("job")
    except ValueError:
        return None

    # Need a `site` segment before `job` and an `externalPath` after it.
    if job_idx == 0 or job_idx == len(segments) - 1:
        return None

    site = segments[job_idx - 1]
    external_path = "/".join(segments[job_idx + 1 :])
    return (
        f"{parsed.scheme}://{parsed.netloc}/wday/cxs/{tenant}/{site}"
        f"/job/{external_path}"
    )


def _workday_info_to_text(payload: dict) -> str:
    """Flatten a Workday CXS payload into clean, plain JD text.

    Joins the posting title with the tag-stripped/entity-decoded HTML body from
    ``jobPostingInfo``.

    Args:
        payload: The JSON object returned by the Workday CXS API for one job.

    Returns:
        The job description as plain text, with HTML tags/entities removed.
    """
    info = payload.get("jobPostingInfo") or {}
    parts: list[str] = []

    title = (info.get("title") or "").strip()
    if title:
        parts.append(title)

    body = _html_to_text(info.get("jobDescription"))
    if body:
        parts.append(body)

    return "\n\n".join(parts).strip()


def _fetch_workday(url: str) -> IntakeResult:
    """Resolve a Workday posting via its public CXS JSON API.

    Builds the CXS URL from the posting URL, fetches the structured JD, and
    flattens it to text. Any failure (unexpected URL shape, HTTP/JSON error,
    non-dict payload, too-short result) degrades to ``NEEDS_PASTE`` so the user
    can paste the description manually.

    Args:
        url: A Workday posting URL.

    Returns:
        An :class:`IntakeResult` — ``OK`` with the JD text on success, otherwise
        ``NEEDS_PASTE`` (always keeping the original *url* as the link).
    """
    cxs_url = _workday_cxs_url(url)
    if cxs_url is None:
        logger.warning("intake: workday URL has unexpected path: %s", url)
        return IntakeResult(IntakeStatus.NEEDS_PASTE, text=None, link=url)

    try:
        resp = httpx.get(
            cxs_url, follow_redirects=True, timeout=30.0, headers=_JSON_HEADERS
        )
        resp.raise_for_status()
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        # ValueError covers a non-JSON body (httpx .json() raises JSONDecodeError).
        logger.warning("intake: workday API failed for %s: %s", url, exc)
        return IntakeResult(IntakeStatus.NEEDS_PASTE, text=None, link=url)

    if not isinstance(payload, dict):
        logger.warning("intake: workday API returned unexpected shape for %s", url)
        return IntakeResult(IntakeStatus.NEEDS_PASTE, text=None, link=url)

    text = _workday_info_to_text(payload)
    if len(text) < _MIN_JOB_TEXT_CHARS:
        logger.warning(
            "intake: workday API yielded only %d chars for %s", len(text), url
        )
        return IntakeResult(IntakeStatus.NEEDS_PASTE, text=None, link=url)

    logger.info("intake: workday API extracted %d chars from %s", len(text), url)
    return IntakeResult(IntakeStatus.OK, text=text, link=url)


# --- Adapter registry ------------------------------------------------------
# Some ATS career pages are JS-rendered SPAs that a generic httpx+trafilatura
# fetch can't read; each has a dedicated adapter that hits its JSON API instead.
# Registering them here keeps extract_job_text clean and makes adding the next
# ATS (Greenhouse/Lever/…) a one-line change.


@dataclass(frozen=True)
class _SiteAdapter:
    """A per-ATS intake adapter.

    Attributes:
        name: Short adapter name, used only for logging.
        matches: Predicate deciding whether this adapter handles a given URL.
        fetch: Resolver turning a matching URL into an :class:`IntakeResult`.
    """

    name: str
    matches: Callable[[str], bool]
    fetch: Callable[[str], IntakeResult]


_ADAPTERS: tuple[_SiteAdapter, ...] = (
    _SiteAdapter("comeet", _is_comeet_url, _fetch_comeet),
    _SiteAdapter("workday", _is_workday_url, _fetch_workday),
)


def _find_url(text: str) -> str | None:
    """Return the first http(s) URL token in *text*, or ``None`` if there is none.

    Splits on whitespace and returns the first token starting with ``http://``
    or ``https://``, so a URL embedded in a multi-line paste is still found.

    Args:
        text: Stripped user input.

    Returns:
        The URL token, or ``None`` when no URL is present.
    """
    for token in text.split():
        if token.startswith(("http://", "https://")):
            return token
    return None


def extract_job_text(user_input: str) -> IntakeResult:
    """Resolve a user message into clean job text plus its source link.

    Branches on the message contents:

    * No URL at all → ``NEEDS_LINK`` (the handler asks the user to include one).
    * URL plus pasted job text → ``OK`` using the pasted text verbatim; the URL
      is not fetched (this is the blocked-site path).
    * Bare URL → fetch with httpx and clean with trafilatura. On success →
      ``OK``; on an HTTP error or empty extraction → ``NEEDS_PASTE`` (the URL is
      kept so the handler can ask the user to paste the text). Known JS-rendered
      ATS sites (Comeet, Workday) are a special case and are resolved through
      their registered adapter (:data:`_ADAPTERS`) instead of the generic fetch.

    Args:
        user_input: The raw message text the user sent (a URL, raw job text, or
            both together).

    Returns:
        An :class:`IntakeResult`. Every ``OK`` result carries a non-null link.
    """
    stripped = user_input.strip()

    url = _find_url(stripped)
    if url is None:
        logger.info("intake: no URL in message — needs link")
        return IntakeResult(IntakeStatus.NEEDS_LINK, text=None, link=None)

    remainder = stripped.replace(url, "", 1).strip()
    if remainder:
        # User pasted the job text alongside the link (blocked-site path); trust
        # the pasted text and skip the fetch entirely.
        logger.info("intake: using pasted text with link %s", url)
        return IntakeResult(IntakeStatus.OK, text=remainder, link=url)

    # Bare URL. Some ATS pages are JS-rendered SPAs, so a generic fetch only sees
    # the empty app shell — route them through their dedicated JSON-API adapter.
    for adapter in _ADAPTERS:
        if adapter.matches(url):
            logger.info("intake: using %s adapter for %s", adapter.name, url)
            return adapter.fetch(url)

    # Bare URL: fetch and clean it.
    logger.info("intake: fetching %s", url)
    try:
        resp = httpx.get(url, follow_redirects=True, timeout=30.0, headers=_HEADERS)
        resp.raise_for_status()
    except httpx.HTTPError as exc:
        logger.warning("intake: fetch failed for %s: %s", url, exc)
        return IntakeResult(IntakeStatus.NEEDS_PASTE, text=None, link=url)

    # The site may have redirected us to a sign-in page (LinkedIn/Indeed do this
    # to header-only clients). trafilatura would happily extract the login text,
    # so reject it before trusting the content.
    final_url = str(resp.url)
    if _is_auth_wall(final_url):
        logger.warning("intake: %s redirected to a login wall (%s)", url, final_url)
        return IntakeResult(IntakeStatus.NEEDS_PASTE, text=None, link=url)

    extracted = (trafilatura.extract(resp.text) or "").strip()
    # Too-short extractions are login walls, cookie notices, or error stubs, not
    # real postings — fall back to asking the user to paste the text.
    if len(extracted) < _MIN_JOB_TEXT_CHARS:
        logger.warning(
            "intake: %s yielded only %d chars — not a usable posting",
            url,
            len(extracted),
        )
        return IntakeResult(IntakeStatus.NEEDS_PASTE, text=None, link=url)

    logger.info("intake: extracted %d chars from %s", len(extracted), url)
    return IntakeResult(IntakeStatus.OK, text=extracted, link=url)
