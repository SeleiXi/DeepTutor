"""Evidence lifecycle for long-term memory entries.

Memory documents stay human-readable Markdown. This module keeps a sibling
``.evidence.json`` ledger keyed by each stable entry id, preserving revisions,
confidence, confirmations, disputes, and supersession without changing the
Markdown format or invalidating existing files.
"""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile
from typing import Any, Literal

from deeptutor.services.memory.document import Document, Entry

EvidenceAction = Literal["confirm", "dispute", "reactivate", "supersede"]

_LEDGER_VERSION = 1
_HISTORY_LIMIT = 50
_DEFAULT_TTL_DAYS = 90
_TTL_DAYS: dict[tuple[str, str], int] = {
    ("L3", "recent"): 30,
    ("L3", "profile"): 180,
    ("L3", "scope"): 365,
    ("L3", "preferences"): 180,
}


def ledger_path(document_path: Path) -> Path:
    return document_path.with_suffix(".evidence.json")


def load(document_path: Path) -> dict[str, Any]:
    path = ledger_path(document_path)
    if not path.exists():
        return {"version": _LEDGER_VERSION, "entries": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"version": _LEDGER_VERSION, "entries": {}}
    entries = data.get("entries")
    if not isinstance(entries, dict):
        entries = {}
    return {"version": _LEDGER_VERSION, "entries": entries}


def reconcile(
    document_path: Path,
    before: Document,
    after: Document,
    *,
    action: str,
    removal_reasons: dict[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Reconcile a before/after document pair into the evidence ledger."""
    moment = now or datetime.now(tz=timezone.utc)
    at = moment.isoformat()
    data = load(document_path)
    records: dict[str, dict[str, Any]] = data["entries"]
    before_by_id = {entry.id: entry for entry in before.all_entries()}
    after_by_id = {entry.id: entry for entry in after.all_entries()}

    # Bootstrap legacy entries first so a removal on the first ledger-aware
    # write still has a preserved prior revision.
    for entry in before_by_id.values():
        records.setdefault(entry.id, _new_record(entry, at))

    added_ids: list[str] = []
    removed_ids: list[str] = []
    for entry_id, entry in after_by_id.items():
        record = records.get(entry_id)
        if record is None:
            records[entry_id] = _new_record(entry, at)
            records[entry_id]["history"].append(
                {"at": at, "action": action, "text": entry.text, "refs": list(entry.refs)}
            )
            added_ids.append(entry_id)
            continue

        changed = (
            record.get("text_hash") != _text_hash(entry.text)
            or list(record.get("refs") or []) != list(entry.refs)
            or record.get("section") != entry.section
        )
        if changed:
            _append_history(
                record,
                {
                    "at": at,
                    "action": "revision",
                    "source_action": action,
                    "text": str(record.get("current_text") or ""),
                    "refs": list(record.get("refs") or []),
                },
            )
            record["revision"] = int(record.get("revision") or 1) + 1
            record["confidence"] = _initial_confidence(entry.refs)
            record["state"] = "active"
            record["dispute_reason"] = ""
            record["last_confirmed_at"] = at
        elif set(entry.refs) - set(record.get("refs") or []):
            record["confidence"] = min(1.0, float(record.get("confidence") or 0.0) + 0.1)
            record["last_confirmed_at"] = at
        _set_current(record, entry, at)

    reasons = removal_reasons or {}
    for entry_id, entry in before_by_id.items():
        if entry_id in after_by_id:
            continue
        removed_ids.append(entry_id)
        record = records.setdefault(entry_id, _new_record(entry, at))
        reason = reasons.get(entry_id, action)
        record["state"] = "disputed" if reason == "contradicted" else "superseded"
        record["dispute_reason"] = reason
        record["last_seen_at"] = at
        _append_history(record, {"at": at, "action": "removed", "reason": reason})

    _link_likely_replacements(records, before_by_id, after_by_id, removed_ids, added_ids)
    _atomic_write_json(ledger_path(document_path), data)
    return data


def report(
    document_path: Path,
    document: Document,
    *,
    layer: str,
    key: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(tz=timezone.utc)
    data = load(document_path)
    records = data["entries"]
    rows: list[dict[str, Any]] = []
    summary = {
        "active": 0,
        "stale": 0,
        "disputed": 0,
        "superseded": 0,
        "low_confidence": 0,
    }
    current_entries = document.all_entries()
    current_ids = {entry.id for entry in current_entries}
    report_records = [
        deepcopy(records.get(entry.id) or _new_record(entry, moment.isoformat()))
        for entry in current_entries
    ]
    report_records.extend(
        deepcopy(record)
        for entry_id, record in records.items()
        if entry_id not in current_ids
    )
    for record in report_records:
        state = effective_state(record, layer=layer, key=key, now=moment)
        record["effective_state"] = state
        record["present"] = record["entry_id"] in current_ids
        record["injected"] = state == "active" and record["present"]
        record["days_since_confirmed"] = _days_since(record.get("last_confirmed_at"), moment)
        rows.append(record)
        summary[state] += 1
        if float(record.get("confidence") or 0.0) < 0.6:
            summary["low_confidence"] += 1
    return {"summary": summary, "entries": rows}


def safe_document(
    document_path: Path,
    document: Document,
    *,
    layer: str,
    key: str,
    now: datetime | None = None,
) -> Document:
    """Return a copy containing only entries safe to inject into the model."""
    moment = now or datetime.now(tz=timezone.utc)
    records = load(document_path)["entries"]
    safe = Document(title=document.title)
    for section, entries in document.sections:
        kept = []
        for entry in entries:
            record = records.get(entry.id)
            if record is None or effective_state(record, layer=layer, key=key, now=moment) == "active":
                kept.append(deepcopy(entry))
        if kept:
            safe.sections.append((section, kept))
    return safe


def transition(
    document_path: Path,
    entry: Entry,
    action: EvidenceAction,
    *,
    reason: str = "",
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(tz=timezone.utc)
    at = moment.isoformat()
    data = load(document_path)
    records = data["entries"]
    record = records.setdefault(entry.id, _new_record(entry, at))
    if action in {"confirm", "reactivate"}:
        record["state"] = "active"
        record["confidence"] = min(1.0, float(record.get("confidence") or 0.0) + 0.2)
        record["last_confirmed_at"] = at
        record["dispute_reason"] = ""
    elif action == "dispute":
        record["state"] = "disputed"
        record["confidence"] = max(0.0, float(record.get("confidence") or 0.0) - 0.35)
        record["dispute_reason"] = reason or "user disputed"
    else:
        record["state"] = "superseded"
        record["dispute_reason"] = reason or "user superseded"
    record["last_seen_at"] = at
    _append_history(record, {"at": at, "action": action, "reason": reason})
    _atomic_write_json(ledger_path(document_path), data)
    return deepcopy(record)


def effective_state(
    record: dict[str, Any],
    *,
    layer: str,
    key: str,
    now: datetime,
) -> str:
    state = str(record.get("state") or "active")
    if state != "active":
        return state if state in {"stale", "disputed", "superseded"} else "disputed"
    ttl = _TTL_DAYS.get((layer, key), _DEFAULT_TTL_DAYS)
    confidence = float(record.get("confidence") or 0.0)
    source_count = int(record.get("source_count") or 0)
    if confidence >= 0.9 and source_count >= 2:
        ttl *= 2
    age = _days_since(record.get("last_confirmed_at"), now)
    return "stale" if age is not None and age > ttl else "active"


def _new_record(entry: Entry, at: str) -> dict[str, Any]:
    return {
        "entry_id": entry.id,
        "state": "active",
        "confidence": _initial_confidence(entry.refs),
        "first_seen_at": at,
        "last_seen_at": at,
        "last_confirmed_at": at,
        "revision": 1,
        "section": entry.section,
        "current_text": entry.text,
        "text_hash": _text_hash(entry.text),
        "refs": list(entry.refs),
        "source_count": len(set(entry.refs)),
        "supersedes": [],
        "superseded_by": "",
        "dispute_reason": "",
        "history": [],
    }


def _set_current(record: dict[str, Any], entry: Entry, at: str) -> None:
    record["section"] = entry.section
    record["current_text"] = entry.text
    record["text_hash"] = _text_hash(entry.text)
    record["refs"] = list(entry.refs)
    record["source_count"] = len(set(entry.refs))
    record["last_seen_at"] = at


def _initial_confidence(refs: list[str]) -> float:
    return min(0.85, 0.45 + 0.1 * len(set(refs)))


def _text_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.lower().split()).encode("utf-8")).hexdigest()


def _append_history(record: dict[str, Any], event: dict[str, Any]) -> None:
    history = record.setdefault("history", [])
    history.append(event)
    if len(history) > _HISTORY_LIMIT:
        del history[: len(history) - _HISTORY_LIMIT]


def _days_since(value: object, now: datetime) -> float | None:
    try:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(0.0, (now - parsed).total_seconds() / 86400)
    except (TypeError, ValueError):
        return None


def _link_likely_replacements(
    records: dict[str, dict[str, Any]],
    before: dict[str, Entry],
    after: dict[str, Entry],
    removed_ids: list[str],
    added_ids: list[str],
) -> None:
    available = set(added_ids)
    for old_id in removed_ids:
        old = before[old_id]
        candidates = [
            (new_id, _similarity(old.text, after[new_id].text))
            for new_id in available
            if after[new_id].section == old.section
        ]
        if not candidates:
            continue
        new_id, score = max(candidates, key=lambda item: item[1])
        if score < 0.3:
            continue
        records[old_id]["superseded_by"] = new_id
        records[new_id].setdefault("supersedes", []).append(old_id)
        available.remove(new_id)


def _similarity(left: str, right: str) -> float:
    left_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", left.lower()))
    right_tokens = set(re.findall(r"[\w\u4e00-\u9fff]+", right.lower()))
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / len(left_tokens | right_tokens)


def _atomic_write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


__all__ = [
    "effective_state",
    "ledger_path",
    "load",
    "reconcile",
    "report",
    "safe_document",
    "transition",
]
