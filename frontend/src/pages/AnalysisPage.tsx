import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, del, downloadFile, errorText, formatDateTime, post } from "../api";
import { useAuth } from "../auth";
import { ChatPanel } from "../components/ChatPanel";
import { ConfirmDialog, ErrorNote, LAYER_DOT, ShortSha, SourceBadge, Spinner, SplitPane, StatusBadge } from "../components/ui";
import { can } from "../permissions";
import { LAYERS, type Analysis, type AnalysisEvent, type Blast, type Layer } from "../types";
import { useWorkbench } from "../workbench";
import { C4Tab } from "./analysis/C4Tab";
import { DependenciesTab } from "./analysis/DependenciesTab";
import { InspectorTab } from "./analysis/InspectorTab";
import { LayersTab } from "./analysis/LayersTab";
import { OverviewTab } from "./analysis/OverviewTab";
import { QualityTab } from "./analysis/QualityTab";
import { TopologyTab } from "./analysis/TopologyTab";

export type C4Level = "context" | "container" | "component" | "dynamic";
const TABS = [
  { key: "overview", icon: "dashboard", label: "Overview" },
  { key: "c4", icon: "account_tree", label: "C4 Architecture" },
  { key: "layers", icon: "layers", label: "Layer Catalogue" },
  { key: "topology", icon: "folder_open", label: "Directory & Layer Topology" },
  { key: "inspector", icon: "hub", label: "Dual-Pane Graph & Code" },
  { key: "quality", icon: "report", label: "Quality & Smells" },
  { key: "dependencies", icon: "inventory_2", label: "Dependencies" },
  { key: "chat", icon: "forum", label: "Chat with Analyst" },
] as const;
const STAGES = ["Stage 1 · Ingestion", "Stage 2 · Macro discovery", "Stage 3 · Micro parsing", "Stage 3 · Code graph", "Stage 3 · Classification", "Stage 4 · Synthesis", "Stage 4 · Layer catalogue", "Stage 4 · Indexing", "Persisting"];

function RunView({ analysis, events, onCancel, canWrite }: { analysis: Analysis; events: AnalysisEvent[]; onCancel: () => void; canWrite: boolean }) {
  const current = STAGES.indexOf(analysis.stage);
  return (
    <div className="mx-auto max-w-5xl space-y-4 p-4">
      <div className="panel">
        <div className="panel-head">
          <span>Temporal-style workflow · {analysis.status}</span>
          {canWrite && <button type="button" className="btn btn-danger h-6" onClick={onCancel}><span className="material-symbols-outlined icon-sm">stop</span>Cancel</button>}
        </div>
        <div className="p-4">
          <div className="flex items-center justify-between font-mono text-code">
            <span className="text-primary">{analysis.stage}</span>
            <span className="tnum text-primary">{Math.round(analysis.progress)}%</span>
          </div>
          <div className="mt-2 h-2 bg-elevated"><div className="h-full bg-primary stripes transition-[width]" style={{ width: `${analysis.progress}%` }} /></div>
          <p className="mt-2 font-mono text-micro text-muted">{analysis.message}</p>
          <ol className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
            {STAGES.map((stage, index) => (
              <li key={stage} className={`border px-2 py-1.5 font-mono text-micro ${index < current ? "border-ok/40 text-ok" : index === current ? "border-primary text-primary" : "border-line text-faint"}`}>
                <span className="material-symbols-outlined icon-sm mr-1">{index < current ? "check" : index === current ? "progress_activity" : "radio_button_unchecked"}</span>
                {stage}
              </li>
            ))}
          </ol>
        </div>
      </div>
      <div className="panel">
        <div className="panel-head"><span>Console stream</span><span>{events.length} events</span></div>
        <div className="max-h-[420px] overflow-auto bg-inset p-3 font-mono text-micro">
          {events.map((event) => (
            <div key={event.event_id} className="flex gap-3 py-0.5">
              <span className="text-faint tnum">{formatDateTime(event.ts).slice(-5)}</span>
              <span className={`w-48 shrink-0 truncate ${event.level === "error" ? "text-critical" : event.level === "warn" ? "text-warning" : "text-primary"}`}>{event.stage}</span>
              <span className={event.level === "error" ? "text-critical" : "text-ink"}>{event.message}</span>
            </div>
          ))}
          {!events.length && <Spinner label="Waiting for a worker…" />}
        </div>
      </div>
    </div>
  );
}

export function AnalysisPage() {
  const { analysisId = "" } = useParams();
  const [params, setParams] = useSearchParams();
  const navigate = useNavigate();
  const { user } = useAuth();
  const { setInfo } = useWorkbench();
  const [analysis, setAnalysis] = useState<Analysis | null>(null);
  const [events, setEvents] = useState<AnalysisEvent[]>([]);
  const [error, setError] = useState("");
  const [layers, setLayers] = useState<Set<Layer>>(new Set(LAYERS));
  const [c4Level, setC4Level] = useState<C4Level>("container");
  const [panelOpen, setPanelOpen] = useState(true);
  const [blast, setBlast] = useState<Blast | null>(null);
  const [deleting, setDeleting] = useState(false);
  const tab = params.get("tab") ?? "overview";
  const canWrite = can(user, "repositories", "write");

  const setTab = useCallback((key: string, extra: Record<string, string> = {}) => {
    const next = new URLSearchParams();
    next.set("tab", key);
    Object.entries(extra).forEach(([k, v]) => next.set(k, v));
    setParams(next);
  }, [setParams]);

  const load = useCallback(async () => {
    const data = await api<Analysis>(`/api/analyses/${analysisId}`);
    setAnalysis(data);
    if (data.status !== "completed") setEvents(await api<AnalysisEvent[]>(`/api/analyses/${analysisId}/events`));
    return data;
  }, [analysisId]);

  useEffect(() => {
    setAnalysis(null);
    setEvents([]);
    load().catch((err) => setError(errorText(err)));
  }, [load]);

  useEffect(() => {
    if (!analysis || !["queued", "running"].includes(analysis.status)) return;
    const timer = setInterval(() => void load().catch(() => undefined), 1500);
    return () => clearInterval(timer);
  }, [analysis, load]);

  useEffect(() => {
    if (!analysis) return;
    setInfo({
      analysisId: analysis.analysis_id,
      repoName: analysis.repo.name,
      branch: analysis.branch,
      sha: analysis.commit_sha,
      merkle: analysis.merkle_root,
      health: analysis.health_score,
      source: analysis.repo.source_type,
      status: analysis.status,
    });
  }, [analysis, setInfo]);
  useEffect(() => () => setInfo(null), [setInfo]);

  const layerCounts = useMemo(() => analysis?.stats?.layers ?? {}, [analysis]);
  if (!analysis) return <div className="p-6">{error ? <ErrorNote message={error} /> : <Spinner label="Loading analysis…" />}</div>;

  if (analysis.status !== "completed") {
    return (
      <div>
        <div className="flex items-center gap-3 border-b border-line bg-surface px-4 py-2">
          <Link to={`/repositories/${analysis.repo_id}`} className="font-mono text-code hover:text-primary">{analysis.repo.name}</Link>
          <span className="font-mono text-code text-primary">{analysis.branch}</span>
          <StatusBadge status={analysis.status} progress={analysis.progress} />
        </div>
        {["queued", "running"].includes(analysis.status) ? (
          <RunView analysis={analysis} events={events} canWrite={canWrite} onCancel={() => void post(`/api/analyses/${analysisId}/cancel`).then(load)} />
        ) : (
          <div className="mx-auto max-w-5xl space-y-4 p-4">
            <ErrorNote message={analysis.status === "failed" ? analysis.error.split("\n")[0] || "The analysis failed" : "The analysis was cancelled."} />
            {canWrite && (
              <button type="button" className="btn btn-primary" onClick={async () => { const run = await post<{ analysis_id: string }>(`/api/analyses/${analysisId}/rerun`); navigate(`/analyses/${run.analysis_id}`); }}>
                <span className="material-symbols-outlined icon-sm">replay</span>Run again
              </button>
            )}
            <RunView analysis={analysis} events={events} canWrite={false} onCancel={() => undefined} />
          </div>
        )}
      </div>
    );
  }

  const stats = analysis.stats;
  const severities = stats.concerns_by_severity ?? {};
  const telemetryAside = (
    <aside className={`${panelOpen ? "w-full" : "w-10 border-r border-line"} flex h-full shrink-0 flex-col bg-surface`}>
      <div className={`flex h-9 items-center justify-between border-b border-line px-2 ${panelOpen ? "pr-9" : ""}`}>
        {panelOpen && <span className="font-mono text-head-sm">TELEMETRY CONTROL</span>}
        <button type="button" className="icon-btn" onClick={() => setPanelOpen((o) => !o)} title={panelOpen ? "Collapse" : "Expand"}>
          <span className="material-symbols-outlined">{panelOpen ? "left_panel_close" : "left_panel_open"}</span>
        </button>
      </div>
      {panelOpen && (
        <div className="flex-1 space-y-4 overflow-auto p-3">
          <div className="border border-line bg-inset p-2.5">
            <div className="flex items-center justify-between font-mono text-micro">
              <span className="text-muted">WORKFLOW</span>
              <span className="text-ok">● COMPLETED</span>
            </div>
            <div className="mt-1 font-mono text-code"><Link to={`/repositories/${analysis.repo_id}`} className="hover:text-primary">{analysis.repo.name}</Link></div>
            <div className="mt-1 flex items-center gap-2 font-mono text-micro text-faint">
              <span className="text-primary">{analysis.branch || "—"}</span> <ShortSha sha={analysis.commit_sha} />
            </div>
            <div className="mt-1 font-mono text-micro text-faint">{stats.duration_seconds}s · {formatDateTime(analysis.finished_at)}</div>
            <div className="mt-1"><SourceBadge source={analysis.repo.source_type} /></div>
          </div>

          <div>
            <div className="flex items-center justify-between"><span className="label-micro">C4 hierarchy level</span><span className="font-mono text-micro text-primary">{({ context: "L1", container: "L2", component: "L3", dynamic: "L4" } as const)[c4Level]} ACTIVE</span></div>
            <div className="mt-1.5 grid grid-cols-4 border border-line">
              {(["context", "container", "component", "dynamic"] as C4Level[]).map((level, index) => (
                <button key={level} type="button" className={`h-7 font-mono text-micro ${c4Level === level ? "bg-primary/10 text-primary outline outline-1 outline-primary" : "text-muted hover:text-ink"}`} onClick={() => { setC4Level(level); setTab("c4"); }}>
                  L{index + 1} {level.slice(0, 3)}
                </button>
              ))}
            </div>
          </div>

          <div>
            <div className="flex items-center justify-between">
              <span className="label-micro">Canonical layer filters</span>
              <button
                type="button"
                className="font-mono text-micro text-primary hover:underline"
                onClick={() => setLayers((s) => (s.size === LAYERS.length ? new Set() : new Set(LAYERS)))}
              >
                {layers.size === LAYERS.length ? "Clear" : `All (${layers.size}/${LAYERS.length})`}
              </button>
            </div>
            <div className="mt-1.5 space-y-1">
              {LAYERS.map((layer) => (
                <label key={layer} className="flex cursor-pointer items-center gap-2 border border-line px-2 py-1 hover:border-line-strong">
                  <input type="checkbox" className="accent-[rgb(var(--primary))]" checked={layers.has(layer)} onChange={(e) => setLayers((s) => { const next = new Set(s); if (e.target.checked) next.add(layer); else next.delete(layer); return next; })} />
                  <span className={`h-2 w-2 ${LAYER_DOT[layer]}`} />
                  <span className="flex-1 font-mono text-code capitalize">{layer} layer</span>
                  <span className="font-mono text-micro text-faint tnum">{layerCounts[layer] ?? 0} files</span>
                </label>
              ))}
            </div>
          </div>

          <div>
            <span className="label-micro">Audit quick-filters</span>
            <div className="mt-1.5 space-y-1">
              {([
                { icon: "error", label: "Critical concerns", value: severities.critical ?? 0, cls: "text-critical", go: { severity: "critical" } },
                { icon: "cyclone", label: "Tarjan SCC cycles", value: stats.cycles, cls: stats.cycles ? "text-critical" : "text-ok", go: { category: "Tarjan SCC Cycle" } },
                { icon: "stacks", label: "Layer violations", value: stats.layer_violations, cls: stats.layer_violations ? "text-warning" : "text-ok", go: { category: "Layer Violation" } },
                { icon: "deployed_code", label: "God modules", value: stats.god_modules, cls: stats.god_modules ? "text-warning" : "text-ok", go: { category: "God Module" } },
                { icon: "key_off", label: "Hardcoded secrets", value: stats.secrets, cls: stats.secrets ? "text-critical" : "text-ok", go: { category: "Hardcoded Secret" } },
              ] as { icon: string; label: string; value: number; cls: string; go: Record<string, string> }[]).map((item) => (
                <button key={item.label} type="button" className="flex w-full items-center gap-2 border border-line px-2 py-1 text-left hover:border-primary" onClick={() => setTab("quality", item.go)}>
                  <span className={`material-symbols-outlined icon-sm ${item.cls}`}>{item.icon}</span>
                  <span className="flex-1 font-mono text-code">{item.label}</span>
                  <span className={`font-mono text-code tnum ${item.cls}`}>{item.value}</span>
                </button>
              ))}
            </div>
          </div>

          {blast && (
            <div className="border border-line bg-inset p-2.5">
              <div className="flex items-center justify-between font-mono text-micro"><span className="text-primary">◎ BLAST RADIUS</span><span className={blast.risk_label === "LOW" ? "text-ok" : blast.risk_label === "MODERATE" ? "text-warning" : "text-critical"}>{blast.risk_label}</span></div>
              <div className="mt-1 truncate font-mono text-code" title={blast.path}>{blast.path.split("/").pop()}</div>
              <div className="mt-1 flex justify-between font-mono text-micro text-muted"><span>Risk score</span><span className="tnum">{blast.risk.toFixed(2)}</span></div>
              <div className="mt-1 h-1 bg-elevated"><div className={`h-full ${blast.risk < 0.25 ? "bg-ok" : blast.risk < 0.5 ? "bg-warning" : "bg-critical"}`} style={{ width: `${blast.risk * 100}%` }} /></div>
              <div className="mt-1 font-mono text-micro text-faint">{blast.impacted_count} reverse dependants · {blast.direct} direct</div>
            </div>
          )}

          <div className="space-y-1.5 border-t border-line pt-3">
            {canWrite && (
              <button type="button" className="btn btn-primary w-full justify-center" onClick={async () => { const run = await post<{ analysis_id: string }>(`/api/analyses/${analysisId}/rerun`); navigate(`/analyses/${run.analysis_id}`); }}>
                <span className="material-symbols-outlined icon-sm">play_arrow</span>Re-run analysis
              </button>
            )}
            <button type="button" className="btn w-full justify-center" onClick={() => void downloadFile(`/api/analyses/${analysisId}/concerns.md`, `CONCERNS-${analysis.repo.slug || "repo"}.md`)}>
              <span className="material-symbols-outlined icon-sm">download</span>Export CONCERNS.md
            </button>
            {canWrite && (
              <button type="button" className="btn btn-danger w-full justify-center" onClick={() => setDeleting(true)}>
                <span className="material-symbols-outlined icon-sm">delete</span>Delete analysis
              </button>
            )}
          </div>
        </div>
      )}
    </aside>
  );

  const mainSection = (
    <section className="flex h-full min-w-0 flex-1 flex-col">
      <nav className="flex shrink-0 overflow-x-auto border-b border-line bg-surface pr-9">
        {TABS.filter((t) => t.key !== "chat" || can(user, "insights")).map((t) => (
          <button key={t.key} type="button" className={`tab ${tab === t.key ? "tab-active" : ""}`} onClick={() => setTab(t.key)}>
            <span className="material-symbols-outlined icon-sm">{t.icon}</span>
            {t.label}
          </button>
        ))}
      </nav>
      <div className="min-h-0 flex-1 overflow-auto">
        {tab === "overview" && <OverviewTab analysis={analysis} layers={layers} onOpenFile={(path) => setTab("inspector", { file: path })} />}
        {tab === "c4" && <C4Tab analysis={analysis} level={c4Level} layers={layers} onLevel={setC4Level} />}
        {tab === "topology" && <TopologyTab analysis={analysis} layers={layers} onOpenFile={(path) => setTab("inspector", { file: path })} />}
        {tab === "inspector" && (
          <InspectorTab
            analysis={analysis}
            layers={layers}
            file={params.get("file")}
            line={params.get("line") ? Number(params.get("line")) : null}
            onSelect={(path, line) => setTab("inspector", line ? { file: path, line: String(line) } : { file: path })}
            onBlast={setBlast}
          />
        )}
        {tab === "quality" && <QualityTab analysis={analysis} layers={layers} severity={params.get("severity") ?? ""} category={params.get("category") ?? ""} onOpenFile={(path) => setTab("inspector", { file: path })} onFilter={(f) => setTab("quality", f)} />}
        {tab === "dependencies" && <DependenciesTab analysis={analysis} />}
        {tab === "layers" && (
          <LayersTab
            analysis={analysis}
            layer={params.get("layer") ?? "api"}
            itemId={params.get("item")}
            view={params.get("view") ?? "list"}
            onNavigate={(extra) => setTab("layers", extra)}
            onOpenFile={(path, line) => setTab("inspector", line ? { file: path, line: String(line) } : { file: path })}
          />
        )}
        {tab === "chat" && <div className="h-full"><ChatPanel scopeType="analysis" scopeId={analysis.analysis_id} /></div>}
      </div>
    </section>
  );

  return (
    <div className="flex h-full min-h-0">
      {panelOpen ? (
        <SplitPane
          direction="horizontal"
          primary="first"
          initialSize={260}
          minSize={200}
          maxSize={480}
          firstTitle="Telemetry Control"
          secondTitle="Analysis Workspace"
          first={telemetryAside}
          second={mainSection}
          className="w-full"
        />
      ) : (
        <>
          {telemetryAside}
          {mainSection}
        </>
      )}
      {deleting && (
        <ConfirmDialog
          title="Delete analysis"
          message="This deletes the stored analysis with its chats and comparisons."
          onClose={() => setDeleting(false)}
          onConfirm={async () => {
            await del(`/api/analyses/${analysisId}`);
            navigate(`/repositories/${analysis.repo_id}`);
          }}
        />
      )}
    </div>
  );
}
