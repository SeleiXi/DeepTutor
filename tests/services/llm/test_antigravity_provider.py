from __future__ import annotations

from types import SimpleNamespace

import pytest

from deeptutor.services.llm.provider_core.antigravity_provider import (
    AntigravityProvider,
    _strip_model_prefix,
)
from deeptutor.services.provider_registry import canonical_provider_name, find_by_name


class Text:
    def __init__(self, text: str) -> None:
        self.text = text


class Thought(Text):
    pass


class _Response:
    @property
    def chunks(self):
        async def stream():
            yield Thought("reason ")
            yield Text("final ")
            yield Text("answer")

        return stream()


class _Agent:
    def __init__(self, config, captured) -> None:
        captured["config"] = config
        self.captured = captured

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        return None

    async def chat(self, prompt):
        self.captured["prompt"] = prompt
        return _Response()


@pytest.mark.asyncio
async def test_antigravity_provider_streams_text_and_reasoning(monkeypatch) -> None:
    captured = {}
    fake_sdk = SimpleNamespace(
        Agent=lambda config: _Agent(config, captured),
        LocalAgentConfig=lambda **kwargs: kwargs,
        CapabilitiesConfig=lambda **kwargs: kwargs,
    )
    monkeypatch.setattr(
        "deeptutor.services.llm.provider_core.antigravity_provider.importlib.import_module",
        lambda _name: fake_sdk,
    )
    text_deltas = []
    reasoning_deltas = []

    async def on_text(value):
        text_deltas.append(value)

    async def on_reasoning(value):
        reasoning_deltas.append(value)

    provider = AntigravityProvider(
        api_key="secret", default_model="antigravity/gemini-test"
    )
    response = await provider.chat_stream(
        messages=[
            {"role": "system", "content": "Be concise"},
            {"role": "user", "content": "Explain vectors"},
        ],
        tools=[{"type": "function"}],
        on_content_delta=on_text,
        on_reasoning_delta=on_reasoning,
    )

    assert response.content == "final answer"
    assert response.reasoning_content == "reason "
    assert text_deltas == ["final ", "answer"]
    assert reasoning_deltas == ["reason "]
    assert captured["config"] == {
        "model": "gemini-test",
        "api_key": "secret",
        "capabilities": {"enabled_tools": []},
    }
    assert "System instructions:\nBe concise" in captured["prompt"]
    assert "DeepTutor owns tool execution" in captured["prompt"]


def test_antigravity_registry_and_aliases() -> None:
    assert canonical_provider_name("google-antigravity") == "antigravity"
    spec = find_by_name("antigravity")
    assert spec is not None
    assert spec.backend == "antigravity"
    assert spec.env_key == "GEMINI_API_KEY"
    assert spec.is_oauth is False
    assert _strip_model_prefix("antigravity/default") == "default"
