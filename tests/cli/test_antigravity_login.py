from __future__ import annotations

from deeptutor_cli import provider_cmd


def test_antigravity_login_opens_google_ai_studio(monkeypatch, capsys) -> None:
    opened = []
    monkeypatch.setattr(provider_cmd.webbrowser, "open", opened.append)

    provider_cmd._login_antigravity()

    output = capsys.readouterr().out
    assert "Google AI Studio" in output
    assert opened == ["https://aistudio.google.com/app/apikey"]
