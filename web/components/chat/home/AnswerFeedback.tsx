"use client";

import { Check, GraduationCap, ThumbsDown, ThumbsUp } from "lucide-react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

import {
  getAnswerFeedback,
  submitAnswerFeedback,
  type AnswerFeedbackVerdict,
} from "@/lib/answer-feedback";

const CHOICES: Array<{
  verdict: AnswerFeedbackVerdict;
  label: string;
  Icon: typeof ThumbsUp;
}> = [
  { verdict: "helpful", label: "Helpful", Icon: ThumbsUp },
  { verdict: "not_helpful", label: "Not helpful", Icon: ThumbsDown },
  { verdict: "learned", label: "I learned it", Icon: GraduationCap },
];

export default function AnswerFeedback({
  sessionId,
  messageId,
  bookId = "",
}: {
  sessionId: string;
  messageId: number;
  bookId?: string;
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState<AnswerFeedbackVerdict | null>(null);
  const [saving, setSaving] = useState<AnswerFeedbackVerdict | null>(null);
  const [saved, setSaved] = useState(false);
  const [failed, setFailed] = useState(false);

  useEffect(() => {
    const controller = new AbortController();
    void getAnswerFeedback(sessionId, messageId, controller.signal)
      .then((feedback) => setSelected(feedback?.verdict ?? null))
      .catch((error: unknown) => {
        if ((error as { name?: string })?.name !== "AbortError") {
          setFailed(true);
        }
      });
    return () => controller.abort();
  }, [messageId, sessionId]);

  async function choose(verdict: AnswerFeedbackVerdict) {
    if (selected === verdict) return;
    const previous = selected;
    setSelected(verdict);
    setSaving(verdict);
    setSaved(false);
    setFailed(false);
    try {
      await submitAnswerFeedback(sessionId, messageId, verdict, bookId);
      setSaved(true);
    } catch {
      setSelected(previous);
      setFailed(true);
    } finally {
      setSaving(null);
    }
  }

  return (
    <div
      className="flex min-w-0 flex-wrap items-center gap-1.5"
      aria-label={t("Was this answer effective?")}
    >
      <span className="mr-0.5 text-[11.5px] text-[var(--muted-foreground)]">
        {t("Was this answer effective?")}
      </span>
      {CHOICES.map(({ verdict, label, Icon }) => {
        const active = selected === verdict;
        return (
          <button
            key={verdict}
            type="button"
            disabled={saving !== null}
            aria-pressed={active}
            onClick={() => void choose(verdict)}
            className={`inline-flex h-7 items-center gap-1 rounded-lg border px-2 text-[11.5px] transition ${
              active
                ? "border-[var(--primary)]/45 bg-[var(--primary)]/10 text-[var(--primary)]"
                : "border-[var(--border)] bg-transparent text-[var(--muted-foreground)] hover:bg-[var(--muted)]/55 hover:text-[var(--foreground)]"
            } disabled:cursor-wait disabled:opacity-60`}
          >
            <Icon className="h-3.5 w-3.5" />
            {t(label)}
          </button>
        );
      })}
      {saved ? (
        <span className="inline-flex items-center gap-1 text-[11px] text-emerald-600 dark:text-emerald-400">
          <Check className="h-3 w-3" />
          {t("Feedback saved")}
        </span>
      ) : null}
      {failed ? (
        <span className="text-[11px] text-[var(--destructive)]">
          {t("Could not save feedback")}
        </span>
      ) : null}
    </div>
  );
}
