"""GET /api/v1/memory/resolve_entry/{id} → (layer, key).

L3 docs cite L2 entries by their ``m_<ULID>`` entry id. The resolver
turns an L3 footnote click into "which L2 surface do I navigate to".
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

pytest.importorskip("fastapi")

FastAPI = pytest.importorskip("fastapi").FastAPI
TestClient = pytest.importorskip("fastapi.testclient").TestClient

memory_router = importlib.import_module("deeptutor.api.routers.memory").router
paths_mod = importlib.import_module("deeptutor.services.memory.paths")
document_mod = importlib.import_module("deeptutor.services.memory.document")


@pytest.fixture
def client(tmp_path: Path, monkeypatch) -> TestClient:
    monkeypatch.setattr(paths_mod, "memory_root", lambda: tmp_path)
    (tmp_path / "L2").mkdir()
    (tmp_path / "L3").mkdir()
    app = FastAPI()
    app.include_router(memory_router, prefix="/api/v1/memory")
    return TestClient(app)


def _seed_l2(tmp_path: Path, surface: str, entry_id: str) -> None:
    doc = document_mod.Document(
        title=f"{surface} memory",
        sections=[
            (
                "Themes",
                [
                    document_mod.Entry(
                        id=entry_id,
                        section="Themes",
                        text="some fact",
                        refs=[f"{surface}:r1"],
                    ),
                ],
            ),
        ],
    )
    (tmp_path / "L2" / f"{surface}.md").write_text(document_mod.serialize(doc), encoding="utf-8")


def test_resolve_entry_returns_owning_surface(client: TestClient, tmp_path: Path) -> None:
    entry_id = "m_01HZK1ABCDEFGHJKMNPQRSTVWX"
    _seed_l2(tmp_path, "notebook", entry_id)

    res = client.get(f"/api/v1/memory/resolve_entry/{entry_id}")
    assert res.status_code == 200
    body = res.json()
    assert body == {"layer": "L2", "key": "notebook", "entry_id": entry_id}


def test_resolve_entry_404_when_missing(client: TestClient) -> None:
    res = client.get("/api/v1/memory/resolve_entry/m_01HZK1ABCDEFGHJKMNPQRSTVWX")
    assert res.status_code == 404


def test_resolve_entry_400_on_bad_id(client: TestClient) -> None:
    res = client.get("/api/v1/memory/resolve_entry/not-an-entry-id")
    assert res.status_code == 400


def test_resolve_entry_first_hit_wins(client: TestClient, tmp_path: Path) -> None:
    """Iteration order is the SURFACES tuple — chat before notebook."""
    entry_id = "m_01HZK1ABCDEFGHJKMNPQRSTVWX"
    _seed_l2(tmp_path, "chat", entry_id)
    _seed_l2(tmp_path, "notebook", entry_id)  # duplicate (shouldn't happen IRL)

    res = client.get(f"/api/v1/memory/resolve_entry/{entry_id}")
    assert res.status_code == 200
    assert res.json()["key"] == "chat"


def test_evidence_endpoints_confirm_dispute_and_correct(
    client: TestClient, tmp_path: Path
) -> None:
    entry_id = "m_01HZK1ABCDEFGHJKMNPQRSTVWX"
    _seed_l2(tmp_path, "chat", entry_id)

    initial = client.get("/api/v1/memory/doc/L2/chat/evidence")
    assert initial.status_code == 200
    assert initial.json()["summary"]["active"] == 1

    disputed = client.post(
        f"/api/v1/memory/doc/L2/chat/entry/{entry_id}/evidence",
        json={"action": "dispute", "reason": "not true"},
    )
    assert disputed.status_code == 200
    assert disputed.json()["entry"]["state"] == "disputed"

    corrected = client.post(
        f"/api/v1/memory/doc/L2/chat/entry/{entry_id}/evidence",
        json={"action": "correct", "text": "corrected fact"},
    )
    assert corrected.status_code == 200
    assert corrected.json()["entry"]["state"] == "active"
    assert corrected.json()["entry"]["revision"] == 2

    doc = client.get("/api/v1/memory/doc/L2/chat").json()["content"]
    assert "corrected fact" in doc
    assert entry_id in doc


def test_reset_removes_evidence_sidecar(client: TestClient, tmp_path: Path) -> None:
    entry_id = "m_01HZK1ABCDEFGHJKMNPQRSTVWX"
    _seed_l2(tmp_path, "chat", entry_id)
    client.post(
        f"/api/v1/memory/doc/L2/chat/entry/{entry_id}/evidence",
        json={"action": "confirm"},
    )
    evidence_path = tmp_path / "L2" / "chat.evidence.json"
    assert evidence_path.exists()

    response = client.post("/api/v1/memory/doc/L2/chat/reset")
    assert response.status_code == 200
    assert response.json()["removed_evidence"] is True
    assert not evidence_path.exists()
