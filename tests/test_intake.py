"""Unit tests for intake.extract_job_text() and its URL detection.

External calls are monkeypatched: httpx.get and trafilatura.extract never hit
the network. The link-required contract is the focus — every OK result must
carry a non-null link — alongside the guards that reject login walls and
too-short extractions.
"""

import httpx
import pytest

from job_application_bot.integrations import intake
from job_application_bot.integrations.intake import IntakeStatus, extract_job_text

_URL = "https://example.com/jobs/123"

# A body long enough to clear intake._MIN_JOB_TEXT_CHARS, simulating a real posting.
_LONG_TEXT = "Senior Python Engineer. " * 40


class FakeResponse:
    """Minimal stand-in for httpx.Response.

    Args:
        text: The raw HTML body.
        url: The final URL after redirects (defaults to the requested URL).
    """

    def __init__(self, text: str, url: str = _URL):
        self.text = text
        self.url = url

    def raise_for_status(self) -> None:
        return None


def test_find_url():
    assert intake._find_url(_URL) == _URL
    assert intake._find_url(f"Job text first line\n{_URL}\nmore text") == _URL
    assert intake._find_url("just some raw job text, no link here") is None


def test_is_auth_wall():
    assert intake._is_auth_wall("https://www.linkedin.com/uas/login?session_redirect=x")
    assert intake._is_auth_wall("https://example.com/authwall")
    assert not intake._is_auth_wall("https://careers.example.com/job-description/?id=9")


def test_raw_text_without_url_needs_link():
    result = extract_job_text("We need a backend engineer who knows FastAPI.")

    assert result.status is IntakeStatus.NEEDS_LINK
    assert result.text is None
    assert result.link is None


def test_url_plus_pasted_text_skips_fetch(monkeypatch):
    def _boom(*args, **kwargs):
        raise AssertionError("httpx.get must not be called on the paste path")

    monkeypatch.setattr(intake.httpx, "get", _boom)

    result = extract_job_text(f"{_URL}\n\n  Senior Python role at Acme.  ")

    assert result.status is IntakeStatus.OK
    assert result.text == "Senior Python role at Acme."
    assert result.link == _URL


def test_bare_url_fetch_success(monkeypatch):
    monkeypatch.setattr(
        intake.httpx, "get", lambda *a, **k: FakeResponse("<html>...</html>")
    )
    monkeypatch.setattr(
        intake.trafilatura, "extract", lambda _html: f"  {_LONG_TEXT}  "
    )

    result = extract_job_text(_URL)

    assert result.status is IntakeStatus.OK
    assert result.text == _LONG_TEXT.strip()
    assert result.link == _URL


def test_bare_url_auth_wall_redirect_needs_paste(monkeypatch):
    # httpx followed a 302 to a login page; even a long extraction must be rejected.
    login_url = "https://www.linkedin.com/uas/login?session_redirect=x"
    monkeypatch.setattr(
        intake.httpx,
        "get",
        lambda *a, **k: FakeResponse("<html>login</html>", url=login_url),
    )
    monkeypatch.setattr(intake.trafilatura, "extract", lambda _html: _LONG_TEXT)

    result = extract_job_text(_URL)

    assert result.status is IntakeStatus.NEEDS_PASTE
    assert result.text is None
    assert result.link == _URL  # original job URL kept, not the login URL


def test_bare_url_too_short_extraction_needs_paste(monkeypatch):
    # The LinkedIn login-wall scenario: a 200 OK but only a short "Sign in…" stub.
    monkeypatch.setattr(
        intake.httpx, "get", lambda *a, **k: FakeResponse("<html>stub</html>")
    )
    monkeypatch.setattr(
        intake.trafilatura, "extract", lambda _html: "Sign in. Join now."
    )

    result = extract_job_text(_URL)

    assert result.status is IntakeStatus.NEEDS_PASTE
    assert result.text is None
    assert result.link == _URL


def test_bare_url_http_error_needs_paste(monkeypatch):
    def _raise(*args, **kwargs):
        raise httpx.HTTPError("403 Forbidden")

    monkeypatch.setattr(intake.httpx, "get", _raise)

    result = extract_job_text(_URL)

    assert result.status is IntakeStatus.NEEDS_PASTE
    assert result.text is None
    assert result.link == _URL


@pytest.mark.parametrize("extracted", [None, ""])
def test_bare_url_empty_extraction_needs_paste(monkeypatch, extracted):
    monkeypatch.setattr(
        intake.httpx, "get", lambda *a, **k: FakeResponse("<html></html>")
    )
    monkeypatch.setattr(intake.trafilatura, "extract", lambda _html: extracted)

    result = extract_job_text(_URL)

    assert result.status is IntakeStatus.NEEDS_PASTE
    assert result.text is None
    assert result.link == _URL


# --- Comeet adapter --------------------------------------------------------

_COMEET_URL = (
    "https://www.comeet.com/jobs/personetics/83.00A/backend-engineer/54.96A?coref=x"
)

# A COMPANY_DATA blob as embedded in a real Comeet page (only the token matters).
_COMEET_PAGE = (
    '<script>var COMPANY_DATA = {"company_uid": "83.00A", '
    '"token": "TESTTOKEN123", "slug": "personetics"};</script>'
    "<div>{{ position.name }}</div>"  # the unrendered template our scraper sees
)

# A position payload (details=true) whose Description is long enough to clear the
# _MIN_JOB_TEXT_CHARS gate after HTML stripping.
_COMEET_PAYLOAD = {
    "name": "Backend Engineer",
    "details": [
        {
            "name": "Requirements",
            "value": "<ul><li>5+ years Python</li></ul>",
            "order": 2,
        },
        {
            "name": "Description",
            "value": "<p>Build&nbsp;scalable backend systems. </p>" * 30,
            "order": 1,
        },
    ],
}


class FakeApiResponse:
    """httpx.Response stand-in supporting both ``.text`` and ``.json()``."""

    def __init__(self, *, text: str = "", json_data=None, url: str = ""):
        self.text = text
        self._json = json_data
        self.url = url

    def raise_for_status(self) -> None:
        return None

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


def test_is_comeet_url():
    assert intake._is_comeet_url(_COMEET_URL)
    assert intake._is_comeet_url("https://www.comeet.co/jobs/x/1/y/2")
    assert not intake._is_comeet_url(_URL)


def test_comeet_details_to_text_strips_html_and_orders():
    text = intake._comeet_details_to_text(
        {
            "name": "Backend Engineer",
            "details": [
                {"name": "Second", "value": "<p>beta</p>", "order": 2},
                {"name": "First", "value": "<b>alpha</b>&nbsp;x", "order": 1},
            ],
        }
    )
    # Name first, then sections by `order`; no HTML tags or raw entities remain.
    assert text.startswith("Backend Engineer")
    assert text.index("First") < text.index("Second")
    assert "alpha x" in text
    assert "<" not in text and "&nbsp;" not in text


def test_comeet_url_uses_careers_api(monkeypatch):
    calls: list[str] = []

    def fake_get(url, *args, **kwargs):
        calls.append(url)
        if "careers-api" in url:
            return FakeApiResponse(json_data=_COMEET_PAYLOAD, url=url)
        return FakeApiResponse(text=_COMEET_PAGE, url=url)

    monkeypatch.setattr(intake.httpx, "get", fake_get)
    # trafilatura must never run for a Comeet URL.
    monkeypatch.setattr(
        intake.trafilatura,
        "extract",
        lambda *_a, **_k: pytest.fail("trafilatura must not run for Comeet"),
    )

    result = extract_job_text(_COMEET_URL)

    assert result.status is IntakeStatus.OK
    assert result.link == _COMEET_URL
    assert "Backend Engineer" in result.text
    assert "Build scalable backend systems." in result.text
    assert "<p>" not in result.text and "&nbsp;" not in result.text

    # Second call is the API, built from the URL UIDs + the scraped token.
    api_call = calls[1]
    assert "careers-api/2.0/company/83.00A/positions/54.96A" in api_call
    assert "token=TESTTOKEN123" in api_call
    assert "details=true" in api_call


def test_comeet_token_not_found_needs_paste(monkeypatch):
    monkeypatch.setattr(
        intake.httpx,
        "get",
        lambda *a, **k: FakeApiResponse(text="<html>no token here</html>"),
    )

    result = extract_job_text(_COMEET_URL)

    assert result.status is IntakeStatus.NEEDS_PASTE
    assert result.text is None
    assert result.link == _COMEET_URL


def test_comeet_api_error_needs_paste(monkeypatch):
    def fake_get(url, *args, **kwargs):
        if "careers-api" in url:
            raise httpx.HTTPError("500 Server Error")
        return FakeApiResponse(text=_COMEET_PAGE)

    monkeypatch.setattr(intake.httpx, "get", fake_get)

    result = extract_job_text(_COMEET_URL)

    assert result.status is IntakeStatus.NEEDS_PASTE
    assert result.text is None
    assert result.link == _COMEET_URL


def test_comeet_short_jd_needs_paste(monkeypatch):
    """A genuine but too-short API result still degrades to NEEDS_PASTE."""

    def fake_get(url, *args, **kwargs):
        if "careers-api" in url:
            return FakeApiResponse(
                json_data={"name": "Role", "details": [{"name": "D", "value": "tiny"}]}
            )
        return FakeApiResponse(text=_COMEET_PAGE)

    monkeypatch.setattr(intake.httpx, "get", fake_get)

    result = extract_job_text(_COMEET_URL)

    assert result.status is IntakeStatus.NEEDS_PASTE


def test_comeet_url_plus_paste_still_trusts_paste(monkeypatch):
    """A Comeet URL pasted with the JD text uses the paste — no API call."""
    monkeypatch.setattr(
        intake.httpx,
        "get",
        lambda *a, **k: pytest.fail("httpx.get must not run on the paste path"),
    )

    result = extract_job_text(f"{_COMEET_URL}\n\nPasted JD body for the role.")

    assert result.status is IntakeStatus.OK
    assert result.text == "Pasted JD body for the role."
    assert result.link == _COMEET_URL


# --- Workday adapter -------------------------------------------------------

_WORKDAY_URL = (
    "https://acme.wd1.myworkdayjobs.com/en-US/AcmeCareers"
    "/job/San-Francisco/Backend-Engineer_R-123"
)

# A CXS payload whose jobDescription is long enough to clear _MIN_JOB_TEXT_CHARS.
_WORKDAY_PAYLOAD = {
    "jobPostingInfo": {
        "title": "Backend Engineer",
        "jobDescription": "<p>Build&nbsp;scalable backend systems. </p>" * 30,
        "location": "San Francisco",
    }
}


def test_is_workday_url():
    assert intake._is_workday_url(_WORKDAY_URL)
    assert intake._is_workday_url("https://acme.wd3.myworkdayjobs.com/x/job/y/z")
    assert not intake._is_workday_url(_URL)


def test_workday_cxs_url_with_locale_prefix():
    cxs = intake._workday_cxs_url(_WORKDAY_URL)
    assert cxs == (
        "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/AcmeCareers"
        "/job/San-Francisco/Backend-Engineer_R-123"
    )


def test_workday_cxs_url_without_locale_prefix():
    url = "https://acme.wd1.myworkdayjobs.com/AcmeCareers/job/Backend-Engineer_R-9"
    cxs = intake._workday_cxs_url(url)
    assert cxs == (
        "https://acme.wd1.myworkdayjobs.com/wday/cxs/acme/AcmeCareers"
        "/job/Backend-Engineer_R-9"
    )


def test_workday_cxs_url_no_job_segment_returns_none():
    assert (
        intake._workday_cxs_url("https://acme.wd1.myworkdayjobs.com/AcmeCareers")
        is None
    )
    # `/job` present but nothing after it → no externalPath.
    assert (
        intake._workday_cxs_url("https://acme.wd1.myworkdayjobs.com/Site/job") is None
    )


def test_workday_url_uses_cxs_api(monkeypatch):
    calls: list[str] = []

    def fake_get(url, *args, **kwargs):
        calls.append(url)
        return FakeApiResponse(json_data=_WORKDAY_PAYLOAD, url=url)

    monkeypatch.setattr(intake.httpx, "get", fake_get)
    # trafilatura must never run for a Workday URL.
    monkeypatch.setattr(
        intake.trafilatura,
        "extract",
        lambda *_a, **_k: pytest.fail("trafilatura must not run for Workday"),
    )

    result = extract_job_text(_WORKDAY_URL)

    assert result.status is IntakeStatus.OK
    assert result.link == _WORKDAY_URL
    assert "Backend Engineer" in result.text
    assert "Build scalable backend systems." in result.text
    assert "<p>" not in result.text and "&nbsp;" not in result.text

    # The single call is the CXS endpoint built from the posting URL.
    assert len(calls) == 1
    assert (
        "/wday/cxs/acme/AcmeCareers/job/San-Francisco/Backend-Engineer_R-123"
        in calls[0]
    )


def test_workday_api_error_needs_paste(monkeypatch):
    def _raise(*args, **kwargs):
        raise httpx.HTTPError("500 Server Error")

    monkeypatch.setattr(intake.httpx, "get", _raise)

    result = extract_job_text(_WORKDAY_URL)

    assert result.status is IntakeStatus.NEEDS_PASTE
    assert result.text is None
    assert result.link == _WORKDAY_URL


def test_workday_malformed_url_needs_paste(monkeypatch):
    # No `/job/` segment → _workday_cxs_url returns None before any fetch.
    monkeypatch.setattr(
        intake.httpx,
        "get",
        lambda *a, **k: pytest.fail("httpx.get must not run for a malformed URL"),
    )
    bad_url = "https://acme.wd1.myworkdayjobs.com/en-US/AcmeCareers"

    result = extract_job_text(bad_url)

    assert result.status is IntakeStatus.NEEDS_PASTE
    assert result.link == bad_url


def test_workday_short_jd_needs_paste(monkeypatch):
    """A genuine but too-short CXS result still degrades to NEEDS_PASTE."""
    monkeypatch.setattr(
        intake.httpx,
        "get",
        lambda *a, **k: FakeApiResponse(
            json_data={"jobPostingInfo": {"title": "Role", "jobDescription": "tiny"}}
        ),
    )

    result = extract_job_text(_WORKDAY_URL)

    assert result.status is IntakeStatus.NEEDS_PASTE


# --- Adapter registry dispatch ---------------------------------------------


def test_registry_dispatches_to_correct_adapter(monkeypatch):
    """A generic bare URL bypasses every adapter and uses trafilatura."""
    monkeypatch.setattr(
        intake.httpx, "get", lambda *a, **k: FakeResponse("<html>...</html>")
    )
    monkeypatch.setattr(intake.trafilatura, "extract", lambda _html: _LONG_TEXT)

    result = extract_job_text(_URL)

    assert result.status is IntakeStatus.OK
    assert result.text == _LONG_TEXT.strip()
    assert result.link == _URL
