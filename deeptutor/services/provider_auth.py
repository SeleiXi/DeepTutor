"""Authentication entry points for LLM providers."""

from __future__ import annotations

from typing import Any

from deeptutor.services.provider_registry import canonical_provider_name

ANTIGRAVITY_LOGIN_URL = "https://aistudio.google.com/app/apikey"


async def start_provider_login(provider: str) -> dict[str, Any]:
    name = canonical_provider_name(provider) or ""
    if name != "antigravity":
        raise ValueError(f"Provider {provider!r} does not support interactive login.")

    return {
        "provider": name,
        "status": "action_required",
        "login_url": ANTIGRAVITY_LOGIN_URL,
        "command": "",
        "message": (
            "Sign in to Google AI Studio, create an API key, then paste it into "
            "this Antigravity provider profile."
        ),
    }


__all__ = ["ANTIGRAVITY_LOGIN_URL", "start_provider_login"]
