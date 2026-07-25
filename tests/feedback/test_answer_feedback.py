from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from deeptutor.api.routers import answer_feedback as feedback_router
from deeptutor.feedback.service import AnswerFeedbackService
from deeptutor.learning.models import (
    KnowledgePoint,
    KnowledgeType,
    LearningModule,
    LearningProgress,
)
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore


def test_feedback_store_keeps_current_state_and_audit_history(tmp_path: Path):
    db_path = tmp_path / "feedback.db"
    service = AnswerFeedbackService(db_path)
    first = service.record(
        session_id="s1",
        message_id=10,
        verdict="helpful",
        provider="openai",
        model="gpt-test",
    )
    second = service.record(
        session_id="s1",
        message_id=10,
        verdict="learned",
        provider="openai",
        model="gpt-test",
    )

    assert first.feedback_id == second.feedback_id
    assert service.get("s1", 10).verdict == "learned"
    summary = service.model_rewards()
    assert summary == [
        {
            "provider": "openai",
            "model": "gpt-test",
            "feedback_count": 1,
            "reward_total": 1.0,
            "reward_mean": 1.0,
        }
    ]

    with service._connect() as conn:
        count = conn.execute("SELECT COUNT(*) FROM answer_feedback_events").fetchone()[0]
    assert count == 2


def test_learned_feedback_is_evidence_but_does_not_cross_mastery_gate(tmp_path: Path):
    store = LearningStore(root=tmp_path)
    progress = LearningProgress(
        book_id="book",
        modules=[
            LearningModule(
                id="m1",
                name="Module",
                order=0,
                knowledge_points=[
                    KnowledgePoint(
                        id="kp1",
                        name="Concept",
                        type=KnowledgeType.CONCEPT,
                        module_id="m1",
                    )
                ],
            )
        ],
        knowledge_types={"kp1": KnowledgeType.CONCEPT},
    )
    service = LearningService(store)
    service.record_answer_feedback(
        progress,
        message_id=11,
        verdict="learned",
        knowledge_point_id="kp1",
    )

    loaded = store.load("book")
    assert loaded is not None
    assert loaded.self_reported_mastery["kp1"] is True
    assert loaded.mastery_levels["kp1"] == 0.6
    assert loaded.qualitative_mastery.get("kp1") is not True
    assert loaded.feedback_reward_total == 1.0


def test_changed_feedback_replaces_current_reward_without_erasing_history(tmp_path: Path):
    store = LearningStore(root=tmp_path)
    progress = LearningProgress(book_id="book")
    service = LearningService(store)
    service.record_answer_feedback(
        progress,
        message_id=12,
        verdict="helpful",
    )
    service.record_answer_feedback(
        progress,
        message_id=12,
        verdict="not_helpful",
    )

    loaded = store.load("book")
    assert loaded is not None
    assert len(loaded.answer_feedback) == 2
    assert loaded.feedback_reward_total == -1.0


class _SessionStore:
    async def get_messages(self, session_id: str):
        assert session_id == "s1"
        return [
            {
                "id": 1,
                "role": "user",
                "metadata": {
                    "request_snapshot": {
                        "capability": "chat",
                        "llmSelection": {
                            "provider": "openai",
                            "model": "gpt-test",
                        },
                    }
                },
                "parent_message_id": None,
            },
            {
                "id": 2,
                "role": "assistant",
                "capability": "chat",
                "metadata": {},
                "parent_message_id": 1,
            },
        ]


def test_feedback_api_attributes_reward_to_assistant_model(tmp_path: Path, monkeypatch):
    service = AnswerFeedbackService(tmp_path / "api-feedback.db")
    monkeypatch.setattr(feedback_router, "_get_session_store", lambda: _SessionStore())
    monkeypatch.setattr(feedback_router, "AnswerFeedbackService", lambda: service)
    app = FastAPI()
    app.include_router(feedback_router.router, prefix="/feedback")
    client = TestClient(app)

    response = client.post(
        "/feedback/s1/messages/2",
        json={"verdict": "not_helpful"},
    )

    assert response.status_code == 200
    feedback = response.json()["feedback"]
    assert feedback["reward"] == -1.0
    assert feedback["provider"] == "openai"
    assert feedback["model"] == "gpt-test"


def test_feedback_api_rejects_user_messages(tmp_path: Path, monkeypatch):
    service = AnswerFeedbackService(tmp_path / "api-feedback.db")
    monkeypatch.setattr(feedback_router, "_get_session_store", lambda: _SessionStore())
    monkeypatch.setattr(feedback_router, "AnswerFeedbackService", lambda: service)
    app = FastAPI()
    app.include_router(feedback_router.router, prefix="/feedback")
    client = TestClient(app)

    response = client.post("/feedback/s1/messages/1", json={"verdict": "helpful"})

    assert response.status_code == 400
