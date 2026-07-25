from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from deeptutor.feedback.service import AnswerFeedbackService
from deeptutor.learning.policy import next_objective
from deeptutor.learning.service import LearningService
from deeptutor.learning.storage import LearningStore

router = APIRouter()


class AnswerFeedbackRequest(BaseModel):
    verdict: Literal["helpful", "not_helpful", "learned"]
    book_id: str = Field(default="", max_length=200)
    knowledge_point_id: str = Field(default="", max_length=200)


def _get_session_store():
    # Lazy import keeps this small router/test surface from eagerly loading
    # every optional LLM provider through the session runtime.
    from deeptutor.services.session import get_session_store

    return get_session_store()


def _string(value: object) -> str:
    return str(value or "").strip()


def _request_snapshot(message: dict) -> dict:
    metadata = message.get("metadata")
    if not isinstance(metadata, dict):
        return {}
    snapshot = metadata.get("request_snapshot")
    return snapshot if isinstance(snapshot, dict) else {}


def _book_id_from_snapshot(snapshot: dict) -> str:
    references = snapshot.get("bookReferences")
    if not isinstance(references, list):
        return ""
    for reference in references:
        if isinstance(reference, dict):
            book_id = _string(reference.get("book_id") or reference.get("bookId"))
            if book_id:
                return book_id
    return ""


@router.post("/{session_id}/messages/{message_id}")
async def record_answer_feedback(
    session_id: str,
    message_id: int,
    payload: AnswerFeedbackRequest,
):
    store = _get_session_store()
    messages = await store.get_messages(session_id)
    by_id = {int(message["id"]): message for message in messages if message.get("id") is not None}
    assistant = by_id.get(message_id)
    if assistant is None:
        raise HTTPException(status_code=404, detail="Message not found")
    if assistant.get("role") != "assistant":
        raise HTTPException(status_code=400, detail="Feedback is only accepted for assistant answers")

    parent_id = assistant.get("parent_message_id")
    parent = by_id.get(int(parent_id)) if parent_id is not None else None
    snapshot = _request_snapshot(parent or {})
    selection = snapshot.get("llmSelection")
    selection = selection if isinstance(selection, dict) else {}
    provider = _string(selection.get("provider") or selection.get("binding"))
    model = _string(selection.get("model"))
    capability = _string(assistant.get("capability") or snapshot.get("capability"))
    book_id = _string(payload.book_id) or _book_id_from_snapshot(snapshot)
    knowledge_point_id = _string(payload.knowledge_point_id)

    if book_id:
        try:
            learning_store = LearningStore()
            progress = learning_store.load(book_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if progress is not None:
            if not knowledge_point_id:
                knowledge_point_id = next_objective(progress).knowledge_point_id
            LearningService(learning_store).record_answer_feedback(
                progress,
                message_id=message_id,
                verdict=payload.verdict,
                knowledge_point_id=knowledge_point_id,
            )

    feedback_service = AnswerFeedbackService()
    feedback = feedback_service.record(
        session_id=session_id,
        message_id=message_id,
        verdict=payload.verdict,
        capability=capability,
        provider=provider,
        model=model,
        book_id=book_id,
        knowledge_point_id=knowledge_point_id,
    )
    return {"feedback": feedback_service.to_dict(feedback)}


@router.get("/{session_id}/messages/{message_id}")
async def get_answer_feedback(session_id: str, message_id: int):
    feedback_service = AnswerFeedbackService()
    feedback = feedback_service.get(session_id, message_id)
    return {"feedback": feedback_service.to_dict(feedback) if feedback is not None else None}


@router.get("/rewards/models")
async def get_model_reward_summary():
    return {"models": AnswerFeedbackService().model_rewards()}


__all__ = ["router"]
