"""Unit tests for the OpenAI and Anthropic AIModel implementations.

No network: the SDK client classes (model.OpenAI / model.Anthropic) and
model.get_settings are monkeypatched with fakes that record how they were
called and return canned response objects shaped like the real SDK responses.
"""

import json
from types import SimpleNamespace

import pytest

from job_application_bot.ai import model
from job_application_bot.schema import JobAnalysis

_ANALYSIS = {
    "is_job_posting": True,
    "company": "Acme",
    "role": "Backend Engineer",
    "technologies": ["Python", "FastAPI"],
    "years_required": 3,
    "match_score": 88,
    "rationale": "Strong backend fit; gap in Kubernetes.",
}


def _settings(**overrides):
    """A stub Settings object exposing only the fields the providers read."""
    base = {
        "openai_api_key": "ok",
        "openai_model": "gpt-4o",
        "openai_base_url": None,
        "anthropic_api_key": "ak",
        "anthropic_model": "claude-sonnet-4-6",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


# --------------------------------------------------------------------------- #
# OpenAI                                                                        #
# --------------------------------------------------------------------------- #


class FakeOpenAI:
    """Records constructor kwargs and chat.completions.create calls."""

    def __init__(self, *, content):
        self.init_kwargs = None
        self.create_calls = []
        self._content = content
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.create_calls.append(kwargs)
                message = SimpleNamespace(content=outer._content)
                return SimpleNamespace(choices=[SimpleNamespace(message=message)])

        self.chat = SimpleNamespace(completions=_Completions())

    def __call__(self, **kwargs):
        # model calls OpenAI(api_key=..., base_url=...); capture and return self.
        self.init_kwargs = kwargs
        return self


def _patch_openai(monkeypatch, fake, **settings_overrides):
    monkeypatch.setattr(model, "OpenAI", fake)
    monkeypatch.setattr(model, "get_settings", lambda: _settings(**settings_overrides))


def test_openai_complete_json_uses_json_mode_and_parses(monkeypatch):
    fake = FakeOpenAI(content=json.dumps(_ANALYSIS))
    _patch_openai(monkeypatch, fake)

    result = model.OpenAIAIModel().complete_json(
        "Analyze this.", schema=JobAnalysis, system="You are a recruiter."
    )

    assert result == _ANALYSIS
    call = fake.create_calls[0]
    assert call["response_format"] == {"type": "json_object"}
    system_msg = call["messages"][0]
    assert system_msg["role"] == "system"
    # The injected schema text (and the literal "JSON") rides in the system turn.
    assert "You are a recruiter." in system_msg["content"]
    assert "JSON" in system_msg["content"]
    assert "match_score" in system_msg["content"]
    assert call["messages"][1] == {"role": "user", "content": "Analyze this."}


def test_openai_base_url_forwarded(monkeypatch):
    fake = FakeOpenAI(content=json.dumps(_ANALYSIS))
    _patch_openai(
        monkeypatch, fake, openai_base_url="https://integrate.api.nvidia.com/v1"
    )

    model.OpenAIAIModel()

    assert fake.init_kwargs["base_url"] == "https://integrate.api.nvidia.com/v1"
    assert fake.init_kwargs["api_key"] == "ok"


def test_openai_base_url_defaults_to_none(monkeypatch):
    fake = FakeOpenAI(content=json.dumps(_ANALYSIS))
    _patch_openai(monkeypatch, fake)  # openai_base_url is None

    model.OpenAIAIModel()

    assert fake.init_kwargs["base_url"] is None


def test_openai_complete_text_strips(monkeypatch):
    fake = FakeOpenAI(content="  Dear Acme team,\n• ...\nBest, Sagiv  ")
    _patch_openai(monkeypatch, fake)

    letter = model.OpenAIAIModel().complete_text("Write it.", system="copywriter")

    assert letter == "Dear Acme team,\n• ...\nBest, Sagiv"
    # No JSON response_format on the plain-text path.
    assert "response_format" not in fake.create_calls[0]


# --------------------------------------------------------------------------- #
# Anthropic                                                                     #
# --------------------------------------------------------------------------- #


class FakeAnthropic:
    """Records constructor kwargs and messages.create calls."""

    def __init__(self, *, text):
        self.init_kwargs = None
        self.create_calls = []
        self._text = text
        outer = self

        class _Messages:
            def create(self, **kwargs):
                outer.create_calls.append(kwargs)
                return SimpleNamespace(content=[SimpleNamespace(text=outer._text)])

        self.messages = _Messages()

    def __call__(self, **kwargs):
        self.init_kwargs = kwargs
        return self


def _patch_anthropic(monkeypatch, fake):
    monkeypatch.setattr(model, "Anthropic", fake)
    monkeypatch.setattr(model, "get_settings", lambda: _settings())


def test_anthropic_complete_json_prefills_and_reassembles(monkeypatch):
    # Reply continues from the "{" prefill, so it omits the leading brace.
    fake = FakeAnthropic(text=json.dumps(_ANALYSIS)[1:])
    _patch_anthropic(monkeypatch, fake)

    result = model.AnthropicAIModel().complete_json(
        "Analyze this.", schema=JobAnalysis, system="You are a recruiter."
    )

    assert result == _ANALYSIS
    call = fake.create_calls[0]
    assert "JSON" in call["system"]
    assert call["messages"][0] == {"role": "user", "content": "Analyze this."}
    # The assistant prefill forces a JSON object.
    assert call["messages"][1] == {"role": "assistant", "content": "{"}


def test_anthropic_complete_text_strips(monkeypatch):
    fake = FakeAnthropic(text="  Dear Acme team,\nBest, Sagiv  ")
    _patch_anthropic(monkeypatch, fake)

    letter = model.AnthropicAIModel().complete_text("Write it.", system="copywriter")

    assert letter == "Dear Acme team,\nBest, Sagiv"
    assert fake.create_calls[0]["system"] == "copywriter"


def test_anthropic_complete_text_omits_system_when_none(monkeypatch):
    fake = FakeAnthropic(text="hi")
    _patch_anthropic(monkeypatch, fake)

    model.AnthropicAIModel().complete_text("Write it.")

    assert "system" not in fake.create_calls[0]


# --------------------------------------------------------------------------- #
# Registry                                                                      #
# --------------------------------------------------------------------------- #


def test_registry_has_new_providers():
    assert model._PROVIDERS["openai"] is model.OpenAIAIModel
    assert model._PROVIDERS["anthropic"] is model.AnthropicAIModel


def test_get_model_rejects_unknown_provider(monkeypatch):
    monkeypatch.setattr(
        model, "get_settings", lambda: SimpleNamespace(provider="mystery")
    )
    model.get_model.cache_clear()
    with pytest.raises(ValueError, match="unknown provider"):
        model.get_model()
    model.get_model.cache_clear()
