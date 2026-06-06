# CLAUDE.md

## Project: job-application-bot

Automated job-application pipeline. It ingests job postings, uses an LLM
(Gemini, OpenAI, or Anthropic — selectable via `PROVIDER`) to extract structured
data and score each job 1–100 against the user's CV, logs everything to an
Airtable CRM, and sends a Telegram alert with a tailored cover letter whenever
the match score clears the alert threshold (**default ≥ 80**).


## User

The user is an **AI engineer who works in Python**. Assume fluency with Python,
typing, async, and the AI/LLM ecosystem — explanations can be technical and
concise, no need to spell out language basics.

## Code style

- **Human-readable first.** Favor clear names, small focused functions, and
  straightforward control flow over clever one-liners.
- **Well-documented.** Every module, public function, and class gets a docstring
  describing what it does, its parameters, and what it returns. Add inline
  comments to explain non-obvious logic, decisions, and any external-API
  quirks — but don't narrate the obvious.
- Use type hints throughout.
- **Lint & format with `ruff`** (handles both): `uv run ruff format` to format,
  `uv run ruff check --fix` to lint. Code should be clean under ruff before it's
  considered done.

## Testing

- **`pytest`**, with tests living in `tests/` (mirroring the module under test,
  e.g. `tests/test_brain.py`).
- Cover the pure logic that's easy to get wrong: match-score parsing, the ≥ 80
  threshold boundary, URL dedupe, and intake's empty-extraction fallback.
- Mock external services (Gemini, Telegram, Airtable) in unit tests; never hit
  live APIs from the test suite. `scripts/check_integrations.py` is the separate,
  manual, real-credential smoke test.
- The async Telegram handler is tested with `pytest-asyncio` (`asyncio_mode =
  "auto"` in `pyproject.toml`), so `async def test_*` functions run without
  per-test markers.
- Run with `uv run pytest`.

## Logging

- Use the **stdlib `logging`** module. Configuration lives in
  `logging_config.py`'s `setup_logging()` (called once from `__main__.py`), which
  installs a per-subsystem, level-colored formatter (`colorama` for the colors);
  get a module-level logger per file via `logging.getLogger(__name__)`.
- Log external-call boundaries (Gemini/Telegram/Airtable requests, intake
  fetches) and errors with context; never log secrets or full CV/job text at
  INFO. Use `print` only in throwaway scripts under `scripts/`.


## Tech stack

- **Python, managed with `uv`** — use uv for everything; do not call `pip` or
  activate a venv manually. Dependencies live in `pyproject.toml` / `uv.lock`.
- **AI brain: pluggable LLM** behind a provider-agnostic `AIModel` interface —
  Gemini (`google-genai`), OpenAI (`openai`, incl. OpenAI-compatible endpoints),
  or Anthropic (`anthropic`), chosen via `PROVIDER`. Structured output uses each
  SDK's JSON/response-schema mechanism.
- **Job intake:** `python-telegram-bot` (async, polling mode — no public webhook).
- **URL → text:** `httpx` + `trafilatura`.
- **CRM:** Airtable via `pyairtable`.
- **Alerts:** the same Telegram bot.
- **Config:** `pydantic-settings` (loads `.env`).
- **RSS stretch goal:** `feedparser` + `apscheduler`.

## How the pipeline works

1. **Trigger** — the user sends a job to the Telegram bot (manual path, primary).
   **A link is mandatory** — it's how the user knows where to apply — so intake
   keys off the URL in the message and resolves one of three outcomes:
   - **Bare URL** → fetched with httpx and cleaned with trafilatura.
   - **URL + pasted job text** (same message) → the fetch is skipped and the
     pasted text is trusted (the blocked-site path for LinkedIn/Indeed etc.).
   - **No URL** → rejected with `NEEDS_LINK`, asking the user to include the URL.
   A URL that can't be scraped — HTTP error, a redirect to a login/auth wall, or a
   too-short extraction (cookie/login stub) — returns `NEEDS_PASTE`, asking the
   user to re-send the URL together with the pasted text. An optional RSS poller
   covers the automatic path.
2. **Brain (LLM)** — one call extracts the entities plus a 1–100 match score,
   comparing the job to `cv.md`. A second call writes a cover letter, run **only**
   when the score is ≥ 80.
3. **Action** — every job is written to the Airtable "Jobs" table with status
   "New". If score ≥ 80, send a Telegram alert (summary + cover letter) and flip
   status to "Alerted".

## Project structure

The code is an installable package under `src/job_application_bot/`, grouped by
concern. Run it with the `job-application-bot` console script (or
`python -m job_application_bot`).

- `src/job_application_bot/__main__.py` — entry point (`main()`); configures
  logging, builds the Telegram Application, and starts polling. Backs the
  `job-application-bot` script.
- `src/job_application_bot/logging_config.py` — `setup_logging()`: installs the
  per-subsystem, level-colored log formatter on the root logger.
- `src/job_application_bot/bot.py` — Telegram transport: the owner guard plus the
  `handle_job` message handler, which buffers the fragments Telegram splits long
  pastes into and flushes them as one job after a quiet window.
- `src/job_application_bot/pipeline.py` — `process_job()`: the intake → brain →
  crm → notify orchestration, plus the in-flight `_active_jobs` concurrency gauge.
- `src/job_application_bot/config.py` — `Settings` loaded from `.env` via
  pydantic-settings; cached `get_settings()`.
- `src/job_application_bot/schema.py` — the `JobAnalysis` pydantic model (structured-output shape).
- `src/job_application_bot/ai/model.py` — provider-agnostic `AIModel` interface +
  Gemini / OpenAI / Anthropic implementations; `get_model()` returns a cached
  (lru_cache) instance so the client is built once.
- `src/job_application_bot/ai/brain.py` — `analyze()` (extract + score) and
  `write_cover_letter()`. Loads the CV (`cv.md`, via `PROJECT_ROOT`) and the prompt
  templates, injecting the CV into each via the `{{CV}}` sentinel.
- `src/job_application_bot/prompts/` — the analysis and cover-letter system prompts
  as `*.md` files, loaded as package resources (`importlib.resources`).
- `src/job_application_bot/integrations/intake.py` — turns a message into clean job
  text + its link, returning an `IntakeResult` (`OK` / `NEEDS_PASTE` / `NEEDS_LINK`).
- `src/job_application_bot/integrations/crm.py` — Airtable access: `create()` (logs
  Status=New), `find_by_link()` (URL dedupe lookup), and `update()`. The
  `pyairtable` table is cached via `_get_table` (lru_cache).
- `src/job_application_bot/integrations/notify.py` — Telegram alert formatting and sending.
- `cv.md` — the user's CV as plain text, kept at the repo root (gitignored),
  loaded once at startup.
- `scripts/` — manual, throwaway aids (not part of the pipeline):
  `check_integrations.py` (real-credential integration smoke test).
- `tests/` — pytest suite.
- `.env` — config + secrets (never commit).

## Data contracts

### `brain.analyze()` output (LLM structured response)

- `is_job_posting` — bool; `false` for non-postings (homepages, articles, login
  walls). When `false`, the pipeline replies and skips the Airtable write entirely.
- `company` — string
- `role` — string
- `technologies` — string[]
- `years_required` — int
- `match_score` — int, 1–100
- `rationale` — short string explaining the score

`brain.write_cover_letter()` returns a plain string and is called only when
`match_score >= 80`.

### Airtable "Jobs" table

| Field | Type |
|---|---|
| Company | single line |
| Role | single line |
| Link | URL |
| Tech | long text |
| YearsRequired | number |
| MatchScore | number |
| Rationale | long text |
| Status | single-select: New / Alerted / Applied / Interviewing / Offer / Rejected |
| CoverLetter | long text |
| Added | created time |

The pipeline only ever sets two Status values automatically: `New` on logging
and `Alerted` once the score clears 80. The remaining values (`Applied`,
`Interviewing`, `Offer`, `Rejected`) are manual stages the user moves records
through by hand — the code never reads or writes them.

## Conventions & commands

- Use uv for everything:
  - `uv add <pkg>` — add a dependency (`uv add --dev <pkg>` for dev tools)
  - `uv run <script>` — run a script
  - `uv sync` — install from the lockfile
  - `uv run pytest` — run tests
  - `uv run ruff format` / `uv run ruff check --fix` — format & lint
- Runtime dependencies (see `pyproject.toml`): `google-genai`, `openai`,
  `anthropic`, `python-telegram-bot`, `pyairtable`, `httpx`, `trafilatura`,
  `pydantic`, `pydantic-settings`, `python-dotenv`, `colorama`.
- All config + secrets come from `.env` via `pydantic-settings` (see
  `.env.example` for the full template):
  - `PROVIDER` (`gemini` | `openai` | `anthropic`) — required; selects the AI brain.
  - Per-provider creds, only the selected one needs filling: `GEMINI_API_KEY` /
    `GEMINI_MODEL`, `OPENAI_API_KEY` / `OPENAI_MODEL` / `OPENAI_BASE_URL` (optional,
    for OpenAI-compatible endpoints), `ANTHROPIC_API_KEY` / `ANTHROPIC_MODEL`.
  - `TELEGRAM_BOT_TOKEN`, `CHAT_ID`, `AIRTABLE_TOKEN`, `AIRTABLE_BASE`,
    `AIRTABLE_TABLE` — always required.
  - `ALERT_THRESHOLD` (default 80) and `JOB_TIMEOUT_SECONDS` (default 300) — optional.
- The alert threshold is configurable via `ALERT_THRESHOLD` (default **80**),
  read from `Settings` — reference that single source of truth, never a literal.
- Telegram messages use HTML parse mode; escape dynamic text. Messages cap at
  4096 characters, so cover letters are sent as their own chunked message(s).
- Dedupe by job URL before logging or alerting, so the same posting is never
  processed twice.
- The model name lives in the selected provider's `*_MODEL` env var (`.env`), so
  it's the single source of truth and editable without touching code. A fast model
  is fine for extraction/scoring; check the provider's docs for current names.

## Constraints

- Never commit `.env` or the CV.
- Prefer RSS over scraping LinkedIn/Indeed (anti-bot measures + ToS); treat live
  scraping as a low-priority stretch goal.
- The CV stays as plain text in `cv.md` — no PDF parsing.
- Cover letters are generated only for scores ≥ 80, to save tokens.

## Build phases

1. **Day 1** — scaffold the project and prove all four integrations independently
   (Gemini, Telegram, Airtable, URL→text). No business logic.
2. **Day 2** — build `brain.analyze()` and tune the prompt against real postings.
3. **Day 3** — wire intake → brain → Airtable end to end.
4. **Day 4** — cover-letter generation + Telegram alert + status update.
5. **Day 5** — RSS auto-trigger (stretch) + dedupe, error handling, polish.
6. **Day 6** — buffer: testing, README, demo capture.