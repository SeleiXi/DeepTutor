from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
import sqlite3
import threading
import time
import uuid

from deeptutor.services.path_service import get_path_service

VERDICT_REWARDS: dict[str, float] = {
    "helpful": 0.5,
    "not_helpful": -1.0,
    "learned": 1.0,
}

_schema_lock = threading.Lock()


@dataclass(frozen=True)
class AnswerFeedbackRecord:
    feedback_id: str
    session_id: str
    message_id: int
    verdict: str
    reward: float
    capability: str
    provider: str
    model: str
    book_id: str
    knowledge_point_id: str
    created_at: float
    updated_at: float


class AnswerFeedbackService:
    """Persist one current verdict per answer plus an append-only audit trail."""

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            root = get_path_service().get_workspace_dir() / "feedback"
            root.mkdir(parents=True, exist_ok=True)
            db_path = root / "answer_feedback.db"
        else:
            db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db_path = db_path
        self._ensure_schema()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path, timeout=15)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _ensure_schema(self) -> None:
        with _schema_lock, self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS answer_feedback (
                    session_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    feedback_id TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    reward REAL NOT NULL,
                    capability TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    book_id TEXT NOT NULL DEFAULT '',
                    knowledge_point_id TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    PRIMARY KEY (session_id, message_id)
                );

                CREATE TABLE IF NOT EXISTS answer_feedback_events (
                    event_id TEXT PRIMARY KEY,
                    feedback_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    message_id INTEGER NOT NULL,
                    verdict TEXT NOT NULL,
                    reward REAL NOT NULL,
                    capability TEXT NOT NULL DEFAULT '',
                    provider TEXT NOT NULL DEFAULT '',
                    model TEXT NOT NULL DEFAULT '',
                    book_id TEXT NOT NULL DEFAULT '',
                    knowledge_point_id TEXT NOT NULL DEFAULT '',
                    created_at REAL NOT NULL
                );

                CREATE INDEX IF NOT EXISTS idx_answer_feedback_model
                    ON answer_feedback(provider, model);
                CREATE INDEX IF NOT EXISTS idx_answer_feedback_events_message
                    ON answer_feedback_events(session_id, message_id, created_at);
                """
            )

    def record(
        self,
        *,
        session_id: str,
        message_id: int,
        verdict: str,
        capability: str = "",
        provider: str = "",
        model: str = "",
        book_id: str = "",
        knowledge_point_id: str = "",
    ) -> AnswerFeedbackRecord:
        if verdict not in VERDICT_REWARDS:
            raise ValueError(f"Unsupported feedback verdict: {verdict}")
        now = time.time()
        reward = VERDICT_REWARDS[verdict]
        with self._connect() as conn:
            existing = conn.execute(
                """
                SELECT feedback_id, created_at
                FROM answer_feedback
                WHERE session_id = ? AND message_id = ?
                """,
                (session_id, message_id),
            ).fetchone()
            feedback_id = str(existing["feedback_id"]) if existing else uuid.uuid4().hex
            created_at = float(existing["created_at"]) if existing else now
            values = (
                feedback_id,
                verdict,
                reward,
                capability,
                provider,
                model,
                book_id,
                knowledge_point_id,
                created_at,
                now,
                session_id,
                message_id,
            )
            conn.execute(
                """
                INSERT INTO answer_feedback (
                    feedback_id, verdict, reward, capability, provider, model,
                    book_id, knowledge_point_id, created_at, updated_at,
                    session_id, message_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id, message_id) DO UPDATE SET
                    verdict = excluded.verdict,
                    reward = excluded.reward,
                    capability = excluded.capability,
                    provider = excluded.provider,
                    model = excluded.model,
                    book_id = excluded.book_id,
                    knowledge_point_id = excluded.knowledge_point_id,
                    updated_at = excluded.updated_at
                """,
                values,
            )
            conn.execute(
                """
                INSERT INTO answer_feedback_events (
                    event_id, feedback_id, session_id, message_id, verdict,
                    reward, capability, provider, model, book_id,
                    knowledge_point_id, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    uuid.uuid4().hex,
                    feedback_id,
                    session_id,
                    message_id,
                    verdict,
                    reward,
                    capability,
                    provider,
                    model,
                    book_id,
                    knowledge_point_id,
                    now,
                ),
            )
        return AnswerFeedbackRecord(
            feedback_id=feedback_id,
            session_id=session_id,
            message_id=message_id,
            verdict=verdict,
            reward=reward,
            capability=capability,
            provider=provider,
            model=model,
            book_id=book_id,
            knowledge_point_id=knowledge_point_id,
            created_at=created_at,
            updated_at=now,
        )

    def get(self, session_id: str, message_id: int) -> AnswerFeedbackRecord | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT feedback_id, session_id, message_id, verdict, reward,
                       capability, provider, model, book_id, knowledge_point_id,
                       created_at, updated_at
                FROM answer_feedback
                WHERE session_id = ? AND message_id = ?
                """,
                (session_id, message_id),
            ).fetchone()
        return AnswerFeedbackRecord(**dict(row)) if row else None

    def model_rewards(self) -> list[dict[str, object]]:
        """Return current-feedback aggregates suitable for routing/evaluation."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT provider, model, COUNT(*) AS feedback_count,
                       SUM(reward) AS reward_total, AVG(reward) AS reward_mean
                FROM answer_feedback
                GROUP BY provider, model
                ORDER BY feedback_count DESC, provider, model
                """
            ).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def to_dict(record: AnswerFeedbackRecord) -> dict[str, object]:
        return asdict(record)


__all__ = [
    "AnswerFeedbackRecord",
    "AnswerFeedbackService",
    "VERDICT_REWARDS",
]
