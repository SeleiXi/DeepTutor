"use client";

import { useCallback, useEffect, useState } from "react";
import {
  AlertTriangle,
  Check,
  History,
  Loader2,
  Pencil,
  ShieldCheck,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";

import { apiFetch, apiUrl } from "@/lib/api";

type EvidenceState = "active" | "stale" | "disputed" | "superseded";

interface EvidenceRecord {
  entry_id: string;
  effective_state: EvidenceState;
  confidence: number;
  current_text: string;
  present: boolean;
  injected: boolean;
  revision: number;
  days_since_confirmed: number | null;
  dispute_reason?: string;
}

interface EvidenceResponse {
  summary: Record<EvidenceState | "low_confidence", number>;
  entries: EvidenceRecord[];
}

interface MemoryEvidencePanelProps {
  layer: "L2" | "L3";
  docKey: string;
  refreshToken?: string;
  onChanged?: () => void;
}

export default function MemoryEvidencePanel({
  layer,
  docKey,
  refreshToken,
  onChanged,
}: MemoryEvidencePanelProps) {
  const { t } = useTranslation();
  const [data, setData] = useState<EvidenceResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState("");
  const [editingId, setEditingId] = useState("");
  const [correction, setCorrection] = useState("");
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const response = await apiFetch(
        apiUrl(`/api/v1/memory/doc/${layer}/${docKey}/evidence`),
      );
      if (!response.ok) throw new Error(t("Could not load memory evidence"));
      setData((await response.json()) as EvidenceResponse);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : t("Could not load memory evidence"),
      );
    } finally {
      setLoading(false);
    }
  }, [docKey, layer, t]);

  useEffect(() => {
    void load();
  }, [load, refreshToken]);

  const act = useCallback(
    async (
      entryId: string,
      action: "confirm" | "dispute" | "reactivate" | "correct",
      text = "",
    ) => {
      setBusyId(entryId);
      setError("");
      try {
        const response = await apiFetch(
          apiUrl(
            `/api/v1/memory/doc/${layer}/${docKey}/entry/${entryId}/evidence`,
          ),
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              action,
              text,
              reason: action === "dispute" ? "user marked inaccurate" : "",
            }),
          },
        );
        if (!response.ok) throw new Error(t("Could not update memory evidence"));
        setEditingId("");
        setCorrection("");
        await load();
        onChanged?.();
      } catch (caught) {
        setError(
          caught instanceof Error
            ? caught.message
            : t("Could not update memory evidence"),
        );
      } finally {
        setBusyId("");
      }
    },
    [docKey, layer, load, onChanged, t],
  );

  const entries = data?.entries ?? [];
  const needsReview = entries.filter(
    (entry) =>
      entry.present &&
      (entry.effective_state !== "active" || entry.confidence < 0.6),
  ).length;

  return (
    <section className="shrink-0 rounded-2xl border border-[var(--border)] bg-[var(--card)] p-3">
      <div className="flex items-start justify-between gap-2">
        <div>
          <h2 className="flex items-center gap-1.5 text-[12.5px] font-medium">
            <ShieldCheck className="h-3.5 w-3.5 text-[var(--primary)]" />
            {t("Memory evidence")}
          </h2>
          <p className="mt-0.5 text-[10.5px] text-[var(--muted-foreground)]">
            {t("Only active evidence is used to personalize answers.")}
          </p>
        </div>
        {!loading && entries.length > 0 && (
          <span
            className={
              "rounded-full px-2 py-0.5 text-[10px] " +
              (needsReview
                ? "bg-amber-500/10 text-amber-600 dark:text-amber-400"
                : "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400")
            }
          >
            {needsReview
              ? t("{{count}} to review", { count: needsReview })
              : t("Up to date")}
          </span>
        )}
      </div>

      {loading ? (
        <div className="grid h-16 place-items-center">
          <Loader2 className="h-4 w-4 animate-spin text-[var(--muted-foreground)]" />
        </div>
      ) : entries.length === 0 ? (
        <p className="py-4 text-center text-[11px] text-[var(--muted-foreground)]">
          {t("No evidence-backed entries yet.")}
        </p>
      ) : (
        <div className="mt-3 max-h-56 space-y-2 overflow-y-auto pr-1">
          {entries.map((entry) => {
            const busy = busyId === entry.entry_id;
            const editing = editingId === entry.entry_id;
            return (
              <article
                key={entry.entry_id}
                className="rounded-lg border border-[var(--border)] bg-[var(--background)] p-2"
              >
                <div className="flex items-start gap-2">
                  {entry.effective_state === "active" ? (
                    <Check className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-500" />
                  ) : entry.effective_state === "superseded" ? (
                    <History className="mt-0.5 h-3.5 w-3.5 shrink-0 text-[var(--muted-foreground)]" />
                  ) : (
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0 text-amber-500" />
                  )}
                  <div className="min-w-0 flex-1">
                    <p className="line-clamp-3 text-[11.5px] leading-4">
                      {entry.current_text}
                    </p>
                    <p className="mt-1 text-[9.5px] text-[var(--muted-foreground)]">
                      {t(entry.effective_state)} ·{" "}
                      {Math.round(entry.confidence * 100)}% · r{entry.revision}
                    </p>
                  </div>
                </div>

                {editing && (
                  <div className="mt-2 space-y-1.5">
                    <textarea
                      value={correction}
                      onChange={(event) => setCorrection(event.target.value)}
                      className="min-h-16 w-full resize-y rounded-md border border-[var(--border)] bg-[var(--card)] p-2 text-[11px] outline-none focus:border-[var(--primary)]"
                    />
                    <div className="flex justify-end gap-1">
                      <button
                        type="button"
                        onClick={() => {
                          setEditingId("");
                          setCorrection("");
                        }}
                        className="rounded p-1 hover:bg-[var(--muted)]"
                        aria-label={t("Cancel")}
                      >
                        <X className="h-3.5 w-3.5" />
                      </button>
                      <button
                        type="button"
                        disabled={!correction.trim() || busy}
                        onClick={() =>
                          void act(entry.entry_id, "correct", correction.trim())
                        }
                        className="rounded bg-[var(--primary)] p-1 text-[var(--primary-foreground)] disabled:opacity-40"
                        aria-label={t("Save correction")}
                      >
                        {busy ? (
                          <Loader2 className="h-3.5 w-3.5 animate-spin" />
                        ) : (
                          <Check className="h-3.5 w-3.5" />
                        )}
                      </button>
                    </div>
                  </div>
                )}

                {!editing && entry.present && (
                  <div className="mt-2 flex flex-wrap gap-1">
                    <ActionButton
                      label={
                        entry.effective_state === "active"
                          ? t("Confirm")
                          : t("Reactivate")
                      }
                      disabled={busy}
                      onClick={() =>
                        void act(
                          entry.entry_id,
                          entry.effective_state === "active"
                            ? "confirm"
                            : "reactivate",
                        )
                      }
                    />
                    <ActionButton
                      label={t("Inaccurate")}
                      disabled={busy}
                      onClick={() => void act(entry.entry_id, "dispute")}
                    />
                    <ActionButton
                      label={t("Correct")}
                      icon={Pencil}
                      disabled={busy}
                      onClick={() => {
                        setEditingId(entry.entry_id);
                        setCorrection(entry.current_text);
                      }}
                    />
                  </div>
                )}
              </article>
            );
          })}
        </div>
      )}

      {error && <p className="mt-2 text-[10.5px] text-red-500">{error}</p>}
    </section>
  );
}

function ActionButton({
  label,
  icon: Icon,
  disabled,
  onClick,
}: {
  label: string;
  icon?: typeof Pencil;
  disabled: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      disabled={disabled}
      onClick={onClick}
      className="rounded-md border border-[var(--border)] px-1.5 py-0.5 text-[10px] text-[var(--muted-foreground)] transition hover:bg-[var(--muted)] hover:text-[var(--foreground)] disabled:opacity-40"
    >
      {Icon && <Icon className="mr-1 inline h-2.5 w-2.5" />}
      {label}
    </button>
  );
}
