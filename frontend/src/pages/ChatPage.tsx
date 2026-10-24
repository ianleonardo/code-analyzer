import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatDateTime } from "../api";
import { ChatPanel } from "../components/ChatPanel";
import { PageHeader, Select } from "../components/ui";
import type { AnalysisBrief, ChatSession, Comparison } from "../types";

/** Pick what to talk about (an analysis or a comparison), then chat. */
export function ChatPage() {
  const [params, setParams] = useSearchParams();
  const [analyses, setAnalyses] = useState<AnalysisBrief[]>([]);
  const [comparisons, setComparisons] = useState<Comparison[]>([]);
  const [recent, setRecent] = useState<ChatSession[]>([]);
  const scope = params.get("scope") ?? "";
  const [scopeType, scopeId] = scope.includes(":") ? (scope.split(":") as ["analysis" | "comparison", string]) : ["analysis" as const, ""];

  useEffect(() => {
    api<AnalysisBrief[]>("/api/analyses?status=completed&limit=500").then((rows) => {
      setAnalyses(rows);
    });
    api<Comparison[]>("/api/comparisons").then(setComparisons).catch(() => setComparisons([]));
    api<ChatSession[]>("/api/chat/sessions").then(setRecent).catch(() => setRecent([]));
  }, []);

  useEffect(() => {
    if (!scope && analyses[0]) {
      setParams({ scope: `analysis:${analyses[0].analysis_id}` }, { replace: true });
    }
  }, [scope, analyses, setParams]);

  const label = useMemo(() => {
    if (scopeType === "analysis") {
      const a = analyses.find((x) => x.analysis_id === scopeId);
      return a ? `${a.repo_name}@${a.branch || "-"}` : "";
    }
    return comparisons.find((c) => c.comparison_id === scopeId)?.title ?? "";
  }, [scopeType, scopeId, analyses, comparisons]);

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        icon="forum"
        title="Chat with Analyst"
        subtitle={label ? `Grounded in ${label}` : "Choose an analysis or a comparison"}
        right={
          <Select className="field w-[420px]" value={scope} onChange={(e) => setParams({ scope: e.target.value })}>
            <optgroup label="Analyses">
              {analyses.map((a) => <option key={a.analysis_id} value={`analysis:${a.analysis_id}`}>{a.repo_name} @ {a.branch || "archive"} · {a.commit_sha.slice(0, 7)} · {formatDateTime(a.finished_at)}</option>)}
            </optgroup>
            {comparisons.length > 0 && (
              <optgroup label="Comparisons">
                {comparisons.map((c) => <option key={c.comparison_id} value={`comparison:${c.comparison_id}`}>{c.title}</option>)}
              </optgroup>
            )}
          </Select>
        }
      />
      {recent.length > 0 && (
        <div className="flex gap-2 overflow-x-auto border-b border-line bg-surface px-3 py-1.5">
          <span className="label-micro leading-6">Recent</span>
          {recent.slice(0, 12).map((s) => (
            <button key={s.session_id} type="button" className="chip h-6 border-line text-muted normal-case tracking-normal hover:border-primary hover:text-primary" onClick={() => setParams({ scope: `${s.scope_type}:${s.scope_id}` })}>
              <span className={`material-symbols-outlined icon-sm ${s.is_public ? "text-primary" : "text-faint"}`}>{s.is_public ? "public" : "lock"}</span>
              {s.title.slice(0, 40)}
            </button>
          ))}
        </div>
      )}
      <div className="min-h-0 flex-1">{scopeId ? <ChatPanel key={scope} scopeType={scopeType} scopeId={scopeId} /> : <p className="p-6 text-muted">Run an analysis first, then chat about it here.</p>}</div>
    </div>
  );
}
