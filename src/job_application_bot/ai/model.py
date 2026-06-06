"""Abstract base class and provider implementations for AI models.

Three providers are implemented behind a common :class:`AIModel` interface:

* :class:`GeminiAIModel` — Google Gemini, using its native ``response_schema``
  for structured output.
* :class:`OpenAIAIModel` — OpenAI *and any OpenAI-compatible endpoint* (NVIDIA,
  OpenRouter, Together, ...) selected via a configurable ``base_url``.
* :class:`AnthropicAIModel` — Anthropic's Claude models.

The OpenAI and Anthropic providers don't rely on a vendor-specific structured-
output mode (those vary across OpenAI-compatible endpoints). Instead they inject
the target JSON schema into the system instruction via
:func:`_json_schema_instruction` and parse the reply with ``json.loads``.
"""

import json
import logging
from abc import ABC, abstractmethod
from functools import lru_cache

from anthropic import Anthropic
from google import genai
from google.genai import types
from openai import OpenAI
from pydantic import BaseModel

from job_application_bot.config import get_settings

logger = logging.getLogger(__name__)


def _json_schema_instruction(schema: type[BaseModel]) -> str:
    """Build a system-prompt snippet asking for a JSON object matching *schema*.

    Used by the OpenAI and Anthropic providers, which lack Gemini's native
    response-schema support. Naming "JSON" explicitly also satisfies OpenAI's
    JSON mode, which requires the word to appear somewhere in the messages.

    Args:
        schema: Pydantic model describing the expected response shape.

    Returns:
        An instruction string ending with the schema's JSON Schema definition.
    """
    return (
        "Respond with ONLY a single JSON object — no markdown fences, no prose — "
        "that matches this JSON schema:\n"
        f"{json.dumps(schema.model_json_schema())}"
    )


class AIModel(ABC):
    """Provider-agnostic interface for text and structured-JSON completions."""

    @abstractmethod
    def complete_json(
        self,
        prompt: str,
        *,
        schema: type[BaseModel],
        system: str | None = None,
    ) -> dict:
        """Return a structured response parsed against *schema*.

        Args:
            prompt: The user-turn prompt.
            schema: Pydantic model describing the expected JSON shape.
            system: Optional system instruction.

        Returns:
            A dict whose keys match the fields of *schema*.
        """

    @abstractmethod
    def complete_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
    ) -> str:
        """Return a plain-text completion.

        Args:
            prompt: The user-turn prompt.
            system: Optional system instruction.

        Returns:
            The model's response as a string.
        """


class GeminiAIModel(AIModel):
    """Gemini-backed implementation of :class:`AIModel`.

    Reads ``GEMINI_API_KEY`` and ``GEMINI_MODEL`` from the environment (via
    dotenv) at construction time. The model name is the single source of truth
    in ``.env`` — no hardcoded fallback.
    """

    def __init__(self) -> None:
        """Initialise the Gemini client from application settings."""
        s = get_settings()
        self._model = s.gemini_model
        self._client = genai.Client(api_key=s.gemini_api_key)
        logger.debug("GeminiAIModel initialised with model=%s", self._model)

    def complete_json(
        self,
        prompt: str,
        *,
        schema: type[BaseModel],
        system: str | None = None,
    ) -> dict:
        """Call Gemini with a JSON response schema and return the parsed dict.

        Args:
            prompt: The user-turn prompt.
            schema: Pydantic model used as the response schema.
            system: Optional system instruction passed to Gemini.

        Returns:
            A dict whose keys match the fields of *schema*.

        Raises:
            ValueError: If Gemini returns an empty or unparseable response.
        """
        logger.info(
            "complete_json -> model=%s schema=%s prompt_len=%d",
            self._model,
            schema.__name__,
            len(prompt),
        )
        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=schema,
            **({"system_instruction": system} if system else {}),
        )
        resp = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=config,
        )
        # Prefer resp.parsed (already a Pydantic model) when available.
        if resp.parsed is not None:
            result: dict = resp.parsed.model_dump()
        elif resp.text:
            result = json.loads(resp.text)
        else:
            raise ValueError("Gemini returned an empty response for complete_json")
        logger.info(
            "complete_json <- schema=%s keys=%s",
            schema.__name__,
            list(result.keys()),
        )
        return result

    def complete_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
    ) -> str:
        """Call Gemini for a plain-text completion.

        Args:
            prompt: The user-turn prompt.
            system: Optional system instruction passed to Gemini.

        Returns:
            The model's response as a stripped string.

        Raises:
            ValueError: If Gemini returns an empty response.
        """
        logger.info(
            "complete_text -> model=%s prompt_len=%d",
            self._model,
            len(prompt),
        )
        config = types.GenerateContentConfig(
            **({"system_instruction": system} if system else {})
        )
        resp = self._client.models.generate_content(
            model=self._model,
            contents=prompt,
            config=config,
        )
        if not resp.text:
            raise ValueError("Gemini returned an empty response for complete_text")
        result = resp.text.strip()
        logger.info("complete_text <- response_len=%d", len(result))
        return result


class OpenAIAIModel(AIModel):
    """OpenAI-backed implementation of :class:`AIModel`.

    Works against OpenAI's API and any OpenAI-compatible endpoint. Reads
    ``OPENAI_API_KEY``, ``OPENAI_MODEL`` and an optional ``OPENAI_BASE_URL`` from
    settings. Setting ``OPENAI_BASE_URL`` (e.g.
    ``https://integrate.api.nvidia.com/v1``) plus a matching key and model name is
    all it takes to target NVIDIA, OpenRouter, Together, etc. — there is no
    separate class per vendor.

    Structured output uses portable JSON mode
    (``response_format={"type": "json_object"}``) with the schema injected into the
    system instruction, since native strict outputs aren't supported uniformly
    across OpenAI-compatible endpoints.
    """

    def __init__(self) -> None:
        """Initialise the OpenAI client from application settings."""
        s = get_settings()
        self._model = s.openai_model
        # base_url=None makes the SDK fall back to api.openai.com.
        self._client = OpenAI(
            api_key=s.openai_api_key, base_url=s.openai_base_url or None
        )
        logger.debug(
            "OpenAIAIModel initialised with model=%s custom_base_url=%s",
            self._model,
            bool(s.openai_base_url),
        )

    def complete_json(
        self,
        prompt: str,
        *,
        schema: type[BaseModel],
        system: str | None = None,
    ) -> dict:
        """Call the model in JSON mode and return the parsed dict.

        Args:
            prompt: The user-turn prompt.
            schema: Pydantic model used to build the JSON-schema instruction.
            system: Optional system instruction prepended to the schema instruction.

        Returns:
            A dict whose keys match the fields of *schema*.

        Raises:
            ValueError: If the model returns an empty response.
        """
        logger.info(
            "complete_json -> model=%s schema=%s prompt_len=%d",
            self._model,
            schema.__name__,
            len(prompt),
        )
        system_content = (
            f"{system}\n\n{_json_schema_instruction(schema)}"
            if system
            else _json_schema_instruction(schema)
        )
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system_content},
                {"role": "user", "content": prompt},
            ],
            response_format={"type": "json_object"},
        )
        content = resp.choices[0].message.content
        if not content:
            raise ValueError("OpenAI returned an empty response for complete_json")
        result: dict = json.loads(content)
        logger.info(
            "complete_json <- schema=%s keys=%s",
            schema.__name__,
            list(result.keys()),
        )
        return result

    def complete_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
    ) -> str:
        """Call the model for a plain-text completion.

        Args:
            prompt: The user-turn prompt.
            system: Optional system instruction.

        Returns:
            The model's response as a stripped string.

        Raises:
            ValueError: If the model returns an empty response.
        """
        logger.info("complete_text -> model=%s prompt_len=%d", self._model, len(prompt))
        messages: list[dict] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
        )
        content = resp.choices[0].message.content
        if not content:
            raise ValueError("OpenAI returned an empty response for complete_text")
        result = content.strip()
        logger.info("complete_text <- response_len=%d", len(result))
        return result


# Upper bound on tokens for Anthropic completions. Analyses are small and cover
# letters are capped at ~250 words, so this leaves comfortable headroom.
_ANTHROPIC_MAX_TOKENS = 2048


class AnthropicAIModel(AIModel):
    """Anthropic (Claude) implementation of :class:`AIModel`.

    Reads ``ANTHROPIC_API_KEY`` and ``ANTHROPIC_MODEL`` from settings. Claude has
    no JSON-mode flag, so structured output is forced by injecting the schema into
    the system instruction and prefilling the assistant turn with ``{`` — the
    model then continues a single JSON object, which we reassemble and parse.
    """

    def __init__(self) -> None:
        """Initialise the Anthropic client from application settings."""
        s = get_settings()
        self._model = s.anthropic_model
        self._client = Anthropic(api_key=s.anthropic_api_key)
        logger.debug("AnthropicAIModel initialised with model=%s", self._model)

    def complete_json(
        self,
        prompt: str,
        *,
        schema: type[BaseModel],
        system: str | None = None,
    ) -> dict:
        """Call Claude with an assistant ``{`` prefill and return the parsed dict.

        Args:
            prompt: The user-turn prompt.
            schema: Pydantic model used to build the JSON-schema instruction.
            system: Optional system instruction prepended to the schema instruction.

        Returns:
            A dict whose keys match the fields of *schema*.

        Raises:
            ValueError: If Claude returns an empty response.
        """
        logger.info(
            "complete_json -> model=%s schema=%s prompt_len=%d",
            self._model,
            schema.__name__,
            len(prompt),
        )
        system_content = (
            f"{system}\n\n{_json_schema_instruction(schema)}"
            if system
            else _json_schema_instruction(schema)
        )
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=_ANTHROPIC_MAX_TOKENS,
            system=system_content,
            messages=[
                {"role": "user", "content": prompt},
                # Prefill forces the reply to begin a JSON object; we prepend the
                # "{" back below since the response continues from it.
                {"role": "assistant", "content": "{"},
            ],
        )
        if not resp.content or not resp.content[0].text:
            raise ValueError("Anthropic returned an empty response for complete_json")
        result: dict = json.loads("{" + resp.content[0].text)
        logger.info(
            "complete_json <- schema=%s keys=%s",
            schema.__name__,
            list(result.keys()),
        )
        return result

    def complete_text(
        self,
        prompt: str,
        *,
        system: str | None = None,
    ) -> str:
        """Call Claude for a plain-text completion.

        Args:
            prompt: The user-turn prompt.
            system: Optional system instruction.

        Returns:
            The model's response as a stripped string.

        Raises:
            ValueError: If Claude returns an empty response.
        """
        logger.info("complete_text -> model=%s prompt_len=%d", self._model, len(prompt))
        resp = self._client.messages.create(
            model=self._model,
            max_tokens=_ANTHROPIC_MAX_TOKENS,
            messages=[{"role": "user", "content": prompt}],
            **({"system": system} if system else {}),
        )
        if not resp.content or not resp.content[0].text:
            raise ValueError("Anthropic returned an empty response for complete_text")
        result = resp.content[0].text.strip()
        logger.info("complete_text <- response_len=%d", len(result))
        return result


_PROVIDERS: dict[str, type[AIModel]] = {
    "gemini": GeminiAIModel,
    "openai": OpenAIAIModel,
    "anthropic": AnthropicAIModel,
}


@lru_cache(maxsize=1)
def get_model() -> AIModel:
    """Return an :class:`AIModel` instance for the configured provider.

    Reads the required ``PROVIDER`` setting from the environment and
    instantiates the matching implementation.  Provider selection lives here
    so callers like ``brain.py`` never import a concrete class directly.

    The result is cached (``lru_cache``) so the model — and its underlying
    client — is constructed once per process and reused across every call,
    mirroring :func:`config.get_settings` and ``crm._get_table``.

    Returns:
        A ready-to-use :class:`AIModel` instance.

    Raises:
        ValueError: If ``PROVIDER`` names an unknown provider.
    """
    provider = get_settings().provider.lower()
    cls = _PROVIDERS.get(provider)
    if cls is None:
        known = ", ".join(sorted(_PROVIDERS))
        raise ValueError(f"unknown provider {provider!r} — known providers: {known}")
    logger.debug("get_model: provider=%s", provider)
    return cls()
