"""Unit tests for brain.analyze() and brain.write_cover_letter().

External services are mocked: brain.get_model is monkeypatched to return a fake
AIModel that records how it was called and returns canned responses. No network.
"""

from job_application_bot.ai import brain
from job_application_bot.config import Settings
from job_application_bot.schema import JobAnalysis


class FakeModel:
    """Stand-in for an AIModel that records calls and returns canned responses."""

    def __init__(self, json_result=None, text_result=""):
        self.json_result = json_result or {}
        self.text_result = text_result
        self.json_calls = []
        self.text_calls = []

    def complete_json(self, prompt, *, schema, system=None):
        self.json_calls.append({"prompt": prompt, "schema": schema, "system": system})
        return self.json_result

    def complete_text(self, prompt, *, system=None):
        self.text_calls.append({"prompt": prompt, "system": system})
        return self.text_result


def _patch_model(monkeypatch, fake):
    """Point brain.get_model at *fake* for the duration of a test."""
    monkeypatch.setattr(brain, "get_model", lambda: fake)


def test_threshold_default_and_injected_cv():
    # The alert threshold now lives in config; assert its default without
    # instantiating Settings (which would require a populated .env).
    assert Settings.model_fields["alert_threshold"].default == 80
    assert "<cv>" in brain.ANALYSIS_SYSTEM
    assert "<cv>" in brain._COVER_LETTER_SYSTEM


def test_analyze_wiring_and_passthrough(monkeypatch):
    result = {
        "company": "Acme",
        "role": "Backend Engineer",
        "technologies": ["Python", "FastAPI"],
        "years_required": 3,
        "match_score": 88,
        "rationale": "Strong backend fit; gap in Kubernetes.",
    }
    fake = FakeModel(json_result=result)
    _patch_model(monkeypatch, fake)

    returned = brain.analyze("We need a backend engineer who knows FastAPI.")

    # analyze() returns the model's dict unchanged.
    assert returned == result

    # Wired correctly: JobAnalysis schema, ANALYSIS_SYSTEM, job text in prompt.
    assert len(fake.json_calls) == 1
    call = fake.json_calls[0]
    assert call["schema"] is JobAnalysis
    assert call["system"] == brain.ANALYSIS_SYSTEM
    assert "FastAPI" in call["prompt"]


def test_write_cover_letter_wiring_and_grounded_prompt(monkeypatch):
    fake = FakeModel(text_result="Dear Acme team,\n• ...\nBest, Sagiv")
    _patch_model(monkeypatch, fake)

    analysis = {
        "company": "Acme",
        "role": "Backend Engineer",
        "technologies": ["Python", "FastAPI"],
        "years_required": 3,
        "match_score": 88,
        "rationale": "Strong backend fit via FastAPI; gap in Kubernetes.",
    }
    letter = brain.write_cover_letter("Backend role using Node.js.", analysis)

    assert letter == fake.text_result

    assert len(fake.text_calls) == 1
    call = fake.text_calls[0]
    assert call["system"] == brain._COVER_LETTER_SYSTEM
    # Prompt is personalized from the analysis dict + the raw JD.
    assert "Acme" in call["prompt"]
    assert "Backend Engineer" in call["prompt"]
    assert "FastAPI" in call["prompt"]  # from rationale + technologies
    assert "Node.js" in call["prompt"]  # from the job text
