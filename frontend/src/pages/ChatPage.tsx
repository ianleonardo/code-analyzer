import { useEffect, useMemo, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, formatDateTime } from "../api";
import { ChatPanel } from "../components/ChatPanel";
import { PageHeader, Select } from "../components/ui";
import type { AnalysisBrief, ChatSession, Comparison } from "../types";

type LandscapeBrief = { landscape_id: string; name: string; description?: string };

/** Chat with Analyst: Global cross-repository mode, landscape mode, or grounded in a specific analysis/comparison. */
export function ChatPage() {
  const [params, setParams] = useSearchParams();
  const [analyses, setAnalyses] = useState<AnalysisBrief[]>([]);
  const [comparisons, setComparisons] = useState<Comparison[]>([]);
  const [landscapes, setLandscapes] = useState<LandscapeBrief[]>([]);
  const [recent, setRecent] = useState<ChatSession[]>([]);
  const scope = params.get("scope") ?? "global:all";
  const [scopeType, scopeId] = scope.includes(":")
    ? (scope.split(":") as ["analysis" | "comparison" | "global" | "landscape", string])
    : ["global" as const, "all"];

  useEffect(() => {
    api<AnalysisBrief[]>("/api/analyses?status=completed&limit=500").then(setAnalyses).catch(() => setAnalyses([]));
    api<Comparison[]>("/api/comparisons").then(setComparisons).catch(() => setComparisons([]));
    api<LandscapeBrief[]>("/api/landscapes").then(setLandscapes).catch(() => setLandscapes([]));
    api<ChatSession[]>("/api/chat/sessions").then(setRecent).catch(() => setRecent([]));
  }, []);

  const label = useMemo(() => {
    if (scopeType === "global") {
      return "All Repositories";
    }
    if (scopeType === "landscape") {
      const l = landscapes.find((x) => x.landscape_id === scopeId);
      return l ? `Landscape: ${l.name}` : "Landscape";
    }
    if (scopeType === "analysis") {
      const a = analyses.find((x) => x.analysis_id === scopeId);
      return a ? `${a.repo_name}@${a.branch || "-"}` : "";
    }
    return comparisons.find((c) => c.comparison_id === scopeId)?.title ?? "";
  }, [scopeType, scopeId, analyses, comparisons, landscapes]);

  const subtitle = useMemo(() => {
    if (scopeType === "global") {
      return "Global cross-repository analyst · answers across all repositories, services, and architectures";
    }
    if (scopeType === "landscape") {
      return `Grounded in ${label} · answers cross-service flows, APIs, and shared databases`;
    }
    if (scopeType === "analysis") {
      return label ? `Grounded in repository ${label}` : "Choose an analysis";
    }
    return label ? `Grounded in comparison: ${label}` : "Choose a comparison";
  }, [scopeType, label]);

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        icon="forum"
        title={scopeType === "global" ? "Global Analyst (Cross-Repository)" : "Chat with Analyst"}
        subtitle={subtitle}
        right={
          <Select className="field w-[440px]" value={scope} onChange={(e) => setParams({ scope: e.target.value })}>
            <optgroup label="Cross-Repository">
              <option value="global:all">🌐 Global · All Repositories ({analyses.length})</option>
              {landscapes.map((l) => (
                <option key={l.landscape_id} value={`landscape:${l.landscape_id}`}>🗺️ Landscape: {l.name}</option>
              ))}
            </optgroup>
            {analyses.length > 0 && (
              <optgroup label="Individual Repositories (Analysis Scope)">
                {analyses.map((a) => (
                  <option key={a.analysis_id} value={`analysis:${a.analysis_id}`}>
                    {a.repo_name} @ {a.branch || "archive"} · {a.commit_sha.slice(0, 7)} · {formatDateTime(a.finished_at)}
                  </option>
                ))}
              </optgroup>
            )}
            {comparisons.length > 0 && (
              <optgroup label="Comparisons">
                {comparisons.map((c) => (
                  <option key={c.comparison_id} value={`comparison:${c.comparison_id}`}>
                    ⚖️ {c.title}
                  </option>
                ))}
              </optgroup>
            )}
          </Select>
        }
      />
      {recent.length > 0 && (
        <div className="flex gap-2 overflow-x-auto border-b border-line bg-surface px-3 py-1.5">
          <span className="label-micro leading-6">Recent</span>
          {recent.slice(0, 12).map((s) => (
            <button key={s.session_id} type="button" className="chip h-6 border-line text-muted normal-case tracking-normal hover:border-primary hover:text-primary" onClick={() => setParams({ scope: `${s.scope_type}:${s.scope_id || "all"}` })}>
              <span className={`material-symbols-outlined icon-sm ${s.is_public ? "text-primary" : "text-faint"}`}>{s.is_public ? "public" : "lock"}</span>
              {s.scope_type === "global" && <span className="font-semibold text-primary mr-1">[Global]</span>}
              {s.scope_type === "landscape" && <span className="font-semibold text-accent mr-1">[Landscape]</span>}
              {s.title.slice(0, 36)}
            </button>
          ))}
        </div>
      )}
      <div className="min-h-0 flex-1">
        <ChatPanel key={scope} scopeType={scopeType} scopeId={scopeId} />
      </div>
    </div>
  );
}
