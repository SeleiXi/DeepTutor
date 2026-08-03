from __future__ import annotations

import pytest

from deeptutor.api.routers.settings import _provider_choices
from deeptutor.services import provider_auth


@pytest.mark.asyncio
async def test_antigravity_login_returns_google_ai_studio_entry() -> None:
    result = await provider_auth.start_provider_login("google-antigravity")

    assert result["status"] == "action_required"
    assert result["login_url"] == "https://aistudio.google.com/app/apikey"
    assert result["command"] == ""


def test_antigravity_uses_normal_llm_provider_choices() -> None:
    option = next(item for item in _provider_choices()["llm"] if item["value"] == "antigravity")
    assert option["label"] == "Google Antigravity"
    assert option["login_supported"] is True
    assert option["requires_key"] is True
