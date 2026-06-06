# ---- builder: resolve & install deps into /app/.venv from the lockfile ----
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=0

WORKDIR /app

# Install deps first (cached layer) — only re-runs when lock/manifest change.
RUN --mount=type=cache,target=/root/.cache/uv \
    --mount=type=bind,source=uv.lock,target=uv.lock \
    --mount=type=bind,source=pyproject.toml,target=pyproject.toml \
    uv sync --frozen --no-install-project --no-dev

# Then add the project source and install the package itself (editable).
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# ---- runtime: slim image with just the venv + source ----
FROM python:3.12-slim-bookworm AS runtime

# Run as a non-root user.
RUN useradd --create-home --uid 1000 app
WORKDIR /app

# Bring over the resolved venv and the package source. The editable install
# points the installed `job_application_bot` at /app/src/job_application_bot, which is also what
# makes PROJECT_ROOT == /app so cv.md is found at /app/cv.md.
COPY --from=builder --chown=app:app /app/.venv /app/.venv
COPY --from=builder --chown=app:app /app/src /app/src
COPY --chown=app:app pyproject.toml uv.lock README.md ./

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1

USER app

# Long-lived polling process; the console script defined in pyproject.toml.
CMD ["job-application-bot"]
