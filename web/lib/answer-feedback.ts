import { apiFetch, apiUrl } from "@/lib/api";

export type AnswerFeedbackVerdict = "helpful" | "not_helpful" | "learned";

export interface AnswerFeedbackRecord {
  feedback_id: string;
  session_id: string;
  message_id: number;
  verdict: AnswerFeedbackVerdict;
  reward: number;
  capability: string;
  provider: string;
  model: string;
  book_id: string;
  knowledge_point_id: string;
  created_at: number;
  updated_at: number;
}

async function expectJson<T>(response: Response): Promise<T> {
  if (!response.ok) {
    throw new Error(`Request failed: ${response.status}`);
  }
  return response.json() as Promise<T>;
}

export async function getAnswerFeedback(
  sessionId: string,
  messageId: number,
  signal?: AbortSignal,
): Promise<AnswerFeedbackRecord | null> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/answer-feedback/${encodeURIComponent(sessionId)}/messages/${messageId}`,
    ),
    { cache: "no-store", signal },
  );
  const data = await expectJson<{ feedback: AnswerFeedbackRecord | null }>(
    response,
  );
  return data.feedback;
}

export async function submitAnswerFeedback(
  sessionId: string,
  messageId: number,
  verdict: AnswerFeedbackVerdict,
  bookId = "",
): Promise<AnswerFeedbackRecord> {
  const response = await apiFetch(
    apiUrl(
      `/api/v1/answer-feedback/${encodeURIComponent(sessionId)}/messages/${messageId}`,
    ),
    {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ verdict, book_id: bookId }),
    },
  );
  const data = await expectJson<{ feedback: AnswerFeedbackRecord }>(response);
  return data.feedback;
}
