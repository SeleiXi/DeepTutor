from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from deeptutor.services.memory import evidence, paths, store
from deeptutor.services.memory.document import Document, Entry
from deeptutor.services.memory.store import MemoryStore

ENTRY_A = "m_01HZK4ABCDEFGHJKMNPQRSTVWX"
ENTRY_B = "m_01HZK5ABCDEFGHJKMNPQRSTVWX"


@pytest.fixture
def tmp_memory(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = tmp_path / "memory"
    monkeypatch.setattr(paths, "memory_root", lambda: root)
    paths.ensure_dirs()
    monkeypatch.setattr(store, "_singleton", None)
    return root


def _doc(entry_id: str = ENTRY_A, text: str = "Needs help with fractions") -> Document:
    return Document(
        title="User profile",
        sections=[
            (
                "Knowledge",
                [
                    Entry(
                        id=entry_id,
                        section="Knowledge",
                        text=text,
                        refs=["chat:turn-1"],
                    )
                ],
            )
        ],
    )


def test_reconcile_bootstraps_evidence_ledger(tmp_path: Path) -> None:
    path = tmp_path / "profile.md"
    before = Document(title="User profile")
    after = _doc()

    evidence.reconcile(path, before, after, action="test")

    report = evidence.report(path, after, layer="L3", key="profile")
    assert report["summary"] == {
        "active": 1,
        "stale": 0,
        "disputed": 0,
        "superseded": 0,
        "low_confidence": 1,
    }
    assert report["entries"][0]["revision"] == 1
    assert report["entries"][0]["source_count"] == 1
    assert evidence.ledger_path(path).exists()


def test_stale_and_disputed_entries_are_not_injected(tmp_memory: Path) -> None:
    memory_store = MemoryStore()
    markdown = (
        "# User profile\n\n"
        "## Knowledge\n\n"
        f"- Needs help with fractions [^1] <!--{ENTRY_A}-->\n\n"
        "---\n\n"
        "[^1]: chat:turn-1\n"
    )
    asyncio.run(memory_store.overwrite_doc("L3", "profile", markdown))
    asyncio.run(
        memory_store.update_entry_evidence(
            "L3", "profile", ENTRY_A, action="dispute", reason="incorrect"
        )
    )

    assert "Needs help with fractions" in memory_store.read_raw("L3", "profile")
    assert "Needs help with fractions" not in memory_store.read_l3_concat()
    assert "(No memory available" in memory_store.read_l3_concat()


def test_effective_state_expires_unconfirmed_profile() -> None:
    now = datetime.now(tz=timezone.utc)
    record = {
        "state": "active",
        "confidence": 0.55,
        "source_count": 1,
        "last_confirmed_at": (now - timedelta(days=181)).isoformat(),
    }
    assert (
        evidence.effective_state(record, layer="L3", key="profile", now=now)
        == "stale"
    )


def test_user_correction_preserves_id_and_revision_history(tmp_memory: Path) -> None:
    memory_store = MemoryStore()
    markdown = (
        "# User profile\n\n"
        "## Knowledge\n\n"
        f"- Cannot solve fractions [^1] <!--{ENTRY_A}-->\n\n"
        "---\n\n"
        "[^1]: chat:turn-1\n"
    )
    asyncio.run(memory_store.overwrite_doc("L3", "profile", markdown))

    record = asyncio.run(
        memory_store.update_entry_evidence(
            "L3",
            "profile",
            ENTRY_A,
            action="correct",
            text="Can solve fractions independently",
            reason="user correction",
            refs=["feedback:turn-2"],
        )
    )

    entry = memory_store.read_doc("L3", "profile").find(ENTRY_A)
    assert entry is not None
    assert entry.text == "Can solve fractions independently"
    assert entry.refs == ["feedback:turn-2"]
    assert record["entry_id"] == ENTRY_A
    assert record["revision"] == 2
    assert [event["action"] for event in record["history"]] == [
        "manual_overwrite",
        "revision",
        "confirm",
    ]


def test_removed_entry_remains_in_audit_and_links_replacement(tmp_path: Path) -> None:
    path = tmp_path / "profile.md"
    before = _doc(text="Needs help with solving linear equations")
    after = _doc(
        entry_id=ENTRY_B,
        text="Now solves linear equations with occasional hints",
    )
    evidence.reconcile(path, Document(title="User profile"), before, action="seed")
    evidence.reconcile(path, before, after, action="replace")

    report = evidence.report(path, after, layer="L3", key="profile")
    records = {row["entry_id"]: row for row in report["entries"]}
    assert records[ENTRY_A]["state"] == "superseded"
    assert records[ENTRY_A]["superseded_by"] == ENTRY_B
    assert records[ENTRY_B]["supersedes"] == [ENTRY_A]
    assert report["summary"]["superseded"] == 1
