"""Google Antigravity SDK provider."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
import importlib
from typing import Any

from deeptutor.services.llm.provider_core.base import LLMProvider, LLMResponse

DEFAULT_ANTIGRAVITY_MODEL = "antigravity/default"


class AntigravityProvider(LLMProvider):
    """Expose Antigravity through DeepTutor's ordinary LLM provider contract."""

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str | None = DEFAULT_ANTIGRAVITY_MODEL,
    ) -> None:
        super().__init__(api_key=api_key, api_base=None)
        self.default_model = default_model or DEFAULT_ANTIGRAVITY_MODEL

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        del max_tokens, temperature, reasoning_effort, tool_choice, kwargs
        return await self._run(messages, tools, model)

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        del max_tokens, temperature, reasoning_effort, tool_choice, kwargs
        return await self._run(
            messages,
            tools,
            model,
            on_content_delta=on_content_delta,
            on_reasoning_delta=on_reasoning_delta,
        )

    async def _run(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        model: str | None,
        *,
        on_content_delta: Callable[[str], Awaitable[None]] | None = None,
        on_reasoning_delta: Callable[[str], Awaitable[None]] | None = None,
    ) -> LLMResponse:
        try:
            sdk = _load_sdk()
            prompt = _messages_to_prompt(messages, tools)
            config_kwargs: dict[str, Any] = {}
            selected_model = _strip_model_prefix(model or self.default_model)
            if selected_model and selected_model != "default":
                config_kwargs["model"] = selected_model
            if self.api_key:
                config_kwargs["api_key"] = self.api_key
            capabilities_cls = getattr(sdk, "CapabilitiesConfig", None)
            if capabilities_cls is not None:
                config_kwargs["capabilities"] = capabilities_cls(enabled_tools=[])

            text_parts: list[str] = []
            reasoning_parts: list[str] = []
            async with sdk.Agent(sdk.LocalAgentConfig(**config_kwargs)) as agent:
                response = await agent.chat(prompt)
                async for chunk in response.chunks:
                    text = str(getattr(chunk, "text", "") or "")
                    kind = type(chunk).__name__.lower()
                    if not text:
                        continue
                    if kind == "thought" or "thinking" in kind:
                        reasoning_parts.append(text)
                        if on_reasoning_delta:
                            await on_reasoning_delta(text)
                    elif kind == "text":
                        text_parts.append(text)
                        if on_content_delta:
                            await on_content_delta(text)

            return LLMResponse(
                content="".join(text_parts),
                reasoning_content="".join(reasoning_parts) or None,
                finish_reason="stop",
            )
        except Exception as exc:
            return LLMResponse(content=_friendly_error(exc), finish_reason="error")

    def get_default_model(self) -> str:
        return self.default_model


def _load_sdk():
    try:
        return importlib.import_module("google.antigravity")
    except ImportError as exc:
        raise RuntimeError(
            "google-antigravity is not installed. Install deeptutor[antigravity]."
        ) from exc


def _strip_model_prefix(model: str | None) -> str | None:
    if not model or "/" not in model:
        return model
    prefix, value = model.split("/", 1)
    return value if prefix.lower().replace("-", "_") == "antigravity" else model


def _messages_to_prompt(
    messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None
) -> str:
    sections: list[str] = []
    for message in messages:
        content = _content_text(message.get("content"))
        if not content:
            continue
        role = str(message.get("role") or "user")
        heading = {
            "system": "System instructions",
            "assistant": "Assistant",
            "tool": "Tool result",
        }.get(role, "User")
        sections.append(f"{heading}:\n{content}")
    if tools:
        sections.append(
            "DeepTutor owns tool execution for this turn. Return text only and do not invoke "
            "Antigravity tools."
        )
    return "\n\n".join(sections)


def _content_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return str(content.get("text") or "")
    if isinstance(content, list):
        return "\n".join(filter(None, (_content_text(part) for part in content)))
    return "" if content is None else str(content)


def _friendly_error(exc: Exception) -> str:
    text = str(exc)
    lowered = text.lower()
    if any(marker in lowered for marker in ("credential", "api key", "authenticate", "login")):
        return (
            "Error calling Antigravity: authentication required. Use the Sign in button in "
            "Settings → LLM, run `deeptutor provider login antigravity`, or set GEMINI_API_KEY."
        )
    return f"Error calling Antigravity: {text}"


__all__ = ["AntigravityProvider", "DEFAULT_ANTIGRAVITY_MODEL"]
