"""Application settings loaded from environment variables / .env file."""

from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Which credential fields each provider needs filled in. Used by the
# post-init validator so a misconfigured ``PROVIDER`` fails loudly at startup
# instead of passing ``None`` into an SDK client further down the line.
_REQUIRED_BY_PROVIDER: dict[str, tuple[str, ...]] = {
    "gemini": ("gemini_api_key", "gemini_model"),
    "openai": ("openai_api_key", "openai_model"),
    "anthropic": ("anthropic_api_key", "anthropic_model"),
}


class Settings(BaseSettings):
    """All configuration for the job-application-bot pipeline.

    Fields map to uppercase env vars automatically (e.g. ``gemini_api_key``
    reads ``GEMINI_API_KEY``). Required fields raise ``ValidationError`` at
    startup if missing; optional fields default to ``None``.

    Only the *selected* provider's credentials are required — that check is
    enforced by :meth:`_check_selected_provider` rather than by per-field
    requiredness, so you only fill in the block for the provider you use.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # AI provider selection: gemini | openai | anthropic. Required — there is no
    # default provider, so the choice is always explicit.
    provider: str

    # Telegram
    telegram_bot_token: str
    chat_id: str

    # Airtable CRM
    airtable_token: str
    airtable_base: str
    airtable_table: str

    # Gemini — required only when PROVIDER=gemini (see the validator below).
    gemini_api_key: str | None = None
    gemini_model: str | None = None

    # Anthropic / Claude — required only when PROVIDER=anthropic.
    anthropic_api_key: str | None = None
    anthropic_model: str | None = None

    # OpenAI (and any OpenAI-compatible endpoint, e.g. NVIDIA, OpenRouter) —
    # required only when PROVIDER=openai.
    openai_api_key: str | None = None
    openai_model: str | None = None
    # Point the OpenAI provider at a non-default base URL (e.g. NVIDIA's
    # https://integrate.api.nvidia.com/v1). Leave None for api.openai.com.
    # Genuinely optional even for PROVIDER=openai.
    openai_base_url: str | None = None

    # Match-score cutoff (inclusive) at or above which a job triggers a cover
    # letter + Telegram alert; everything below is logged to the CRM only.
    # Defaults to 80 but the user can pick their own threshold via the env var.
    alert_threshold: int = 80

    # Wall-clock cap (seconds) on a single job through the pipeline. Generous by
    # default because slow local/free models can take minutes per analysis; tune
    # up for slow local models, down for fast hosted ones.
    job_timeout_seconds: int = 300

    @model_validator(mode="after")
    def _check_selected_provider(self) -> "Settings":
        """Ensure the selected provider's credentials are present.

        Runs after the model is populated. Validates that ``provider`` names a
        known provider and that its required credential fields are non-empty,
        raising ``ValueError`` (surfaced as a pydantic ``ValidationError``)
        otherwise.

        Returns:
            The validated settings instance.

        Raises:
            ValueError: If ``provider`` is unknown or its required keys are unset.
        """
        provider = self.provider.lower()
        required = _REQUIRED_BY_PROVIDER.get(provider)
        if required is None:
            known = ", ".join(sorted(_REQUIRED_BY_PROVIDER))
            raise ValueError(f"unknown PROVIDER {self.provider!r} — known: {known}")
        missing = [field.upper() for field in required if not getattr(self, field)]
        if missing:
            raise ValueError(
                f"PROVIDER={provider} requires {', '.join(missing)} in .env"
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the cached application settings, reading .env on first call.

    Returns:
        The singleton :class:`Settings` instance.
    """
    return Settings()
