import type { CatalogDiff, CatalogDiffPart } from "../types";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, del, downloadFile, errorText, formatDateTime, post } from "../api";
import { useAuth } from "../auth";
import { ChatPanel } from "../components/ChatPanel";
import { ConfirmDialog, Empty, ErrorNote, HealthGauge, Markdown, PageHeader, Select, SeverityBadge, ShortSha, Spinner } from "../components/ui";
import { can } from "../permissions";
import type { AnalysisBrief, Comparison } from "../types";
import { LanguageBar } from "./analysis/OverviewTab";

const LOWER_IS_BETTER = new Set(["avg_complexity", "max_complexity", "cycles", "layer_violations", "god_modules", "duplication_pct", "critical", "warning", "info"]);

function AnalysisSelect({ value, onChange, options, label }: { value: string; onChange: (v: string) => void; options: AnalysisBrief[]; label: string }) {
  const groups = useMemo(() => {
    const map: Record<string, AnalysisBrief[]> = {};
    for (const a of options) (map[a.repo_name ?? a.repo_id] ??= []).push(a);
    return map;
  }, [options]);
  return (
    <label className="block flex-1">
      <span className="label-micro">{label}</span>
      <Select className="field mt-1" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">Choose a completed analysis…</option>
        {Object.entries(groups).map(([repo, items]) => (
          <optgroup key={repo} label={repo}>
            {items.map((a) => (
              <option key={a.analysis_id} value={a.analysis_id}>
                {repo} @ {a.branch || "archive"} · {a.commit_sha.slice(0, 7)} · {formatDateTime(a.finished_at)} · health {a.health_score}
              </option>
            ))}
          </optgroup>
        ))}
      </Select>
    </label>
  );
}

export function CompareListPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [list, setList] = useState<Comparison[]>([]);
  const [options, setOptions] = useState<AnalysisBrief[]>([]);
  const [left, setLeft] = useState(params.get("left") ?? "");
  const [right, setRight] = useState(params.get("right") ?? "");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<Comparison | null>(null);
  const canWrite = can(user, "insights", "write");

  const loadList = () => api<Comparison[]>("/api/comparisons").then(setList);

  useEffect(() => {
    loadList();
    api<AnalysisBrief[]>("/api/analyses?status=completed&limit=500").then(setOptions);
  }, []);

  async function create() {
    setBusy(true);
    setError("");
    try {
      const result = await post<Comparison>("/api/comparisons", { left_analysis_id: left, right_analysis_id: right });
      navigate(`/compare/${result.comparison_id}`);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <PageHeader icon="compare_arrows" title="Compare repositories" subtitle="Diff two stored analyses: two repositories, or two branches (master vs release) of the same one." />
      <div className="space-y-4 p-4">
        {canWrite && (
          <section className="panel p-3">
            <div className="flex flex-wrap items-end gap-3">
              <AnalysisSelect label="Left (baseline)" value={left} onChange={setLeft} options={options} />
              <button type="button" className="icon-btn mb-1 h-8 w-8 border border-line" title="Swap" onClick={() => { setLeft(right); setRight(left); }}>
                <span className="material-symbols-outlined">swap_horiz</span>
              </button>
              <AnalysisSelect label="Right (candidate)" value={right} onChange={setRight} options={options} />
              <button type="button" className="btn btn-primary h-8" disabled={!left || !right || left === right || busy} onClick={() => void create()}>
                <span className="material-symbols-outlined icon-sm">compare_arrows</span>{busy ? "Comparing…" : "Compare"}
              </button>
            </div>
            <div className="mt-2"><ErrorNote message={error} /></div>
          </section>
        )}
        <section className="panel">
          <div className="panel-head"><span>Saved comparisons</span></div>
          <table className="w-full">
            <thead className="table-head"><tr><th>Comparison</th><th>By</th><th>Created</th><th className="w-32 text-right">Actions</th></tr></thead>
            <tbody>
              {list.map((c) => (
                <tr key={c.comparison_id} className="table-row">
                  <td><Link className="font-mono text-code hover:text-primary" to={`/compare/${c.comparison_id}`}>{c.title}</Link></td>
                  <td className="text-body-sm">{c.created_by_name}</td>
                  <td className="font-mono text-micro text-faint">{formatDateTime(c.created_at)}</td>
                  <td className="text-right">
                    <div className="inline-flex items-center justify-end gap-1">
                      <button
                        type="button"
                        className="icon-btn hover:text-primary"
                        title="Download Markdown report (.md)"
                        onClick={() => void downloadFile(`/api/comparisons/${c.comparison_id}/report.md`, `COMPARISON-${c.comparison_id.slice(0, 8)}.md`)}
                      >
                        <span className="material-symbols-outlined icon-sm">description</span>
                      </button>
                      <button
                        type="button"
                        className="icon-btn hover:text-primary"
                        title="Download PDF report (.pdf)"
                        onClick={() => void downloadFile(`/api/comparisons/${c.comparison_id}/report.pdf`, `COMPARISON-${c.comparison_id.slice(0, 8)}.pdf`)}
                      >
                        <span className="material-symbols-outlined icon-sm">picture_as_pdf</span>
                      </button>
                      {canWrite && (
                        <button
                          type="button"
                          className="icon-btn hover:!text-critical"
                          title="Delete comparison"
                          onClick={() => setDeleting(c)}
                        >
                          <span className="material-symbols-outlined icon-sm">delete</span>
                        </button>
                      )}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!list.length && <Empty icon="compare_arrows" title="No comparisons yet">Pick two completed analyses above.</Empty>}
        </section>
      </div>
      {deleting && (
        <ConfirmDialog
          title="Delete comparison"
          message={<>Delete comparison “{deleting.title}”?</>}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await del(`/api/comparisons/${deleting.comparison_id}`);
            setDeleting(null);
            void loadList();
          }}
        />
      )}
    </div>
  );
}

function Delta({ metric, delta }: { metric: string; delta: number | null }) {
  if (delta === null || delta === 0) return <span className="text-faint">·</span>;
  const better = LOWER_IS_BETTER.has(metric) ? delta < 0 : metric === "health_score" || metric === "test_files" || metric === "test_ratio" ? delta > 0 : null;
  const cls = better === null ? "text-muted" : better ? "text-ok" : "text-critical";
  return <span className={`tnum ${cls}`}>{delta > 0 ? "+" : ""}{Number.isInteger(delta) ? delta : delta.toFixed(2)}</span>;
}

function Columns({ left, right, common, title }: { left: string[]; right: string[]; common?: string[]; title: string }) {
  return (
    <section className="panel">
      <div className="panel-head"><span>{title}</span></div>
      <div className="grid grid-cols-3 divide-x divide-line">
        {([["Only left", left, "text-critical"], ["Common", common ?? [], "text-muted"], ["Only right", right, "text-ok"]] as const).map(([label, items, cls]) => (
          <div key={label} className="p-2">
            <div className={`font-mono text-micro ${cls}`}>{label} · {items.length}</div>
            <div className="mt-1 flex max-h-56 flex-wrap gap-1 overflow-auto">
              {items.map((item) => <span key={item} className="chip border-line text-ink normal-case tracking-normal">{item}</span>)}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

export function ComparisonPage() {
  const { comparisonId = "" } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [data, setData] = useState<Comparison | null>(null);
  const [tab, setTab] = useState<"report" | "contracts" | "files" | "chat">("report");
  const [fileTab, setFileTab] = useState<"modified" | "added" | "removed">("modified");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState(false);

  useEffect(() => {
    api<Comparison>(`/api/comparisons/${comparisonId}`).then(setData).catch((err) => setError(errorText(err)));
  }, [comparisonId]);
  if (!data) return <div className="p-6">{error ? <ErrorNote message={error} /> : <Spinner />}</div>;
  const r = data.result;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        icon="compare_arrows"
        title={data.title}
        subtitle={r.same_repository ? "Two snapshots of the same repository" : "Two different repositories"}
        right={
          <>
            <button
              type="button"
              className="btn"
              onClick={() => void downloadFile(`/api/comparisons/${comparisonId}/report.md`, `COMPARISON-${comparisonId.slice(0, 8)}.md`)}
            >
              <span className="material-symbols-outlined icon-sm">description</span>Download MD
            </button>
            <button
              type="button"
              className="btn"
              onClick={() => void downloadFile(`/api/comparisons/${comparisonId}/report.pdf`, `COMPARISON-${comparisonId.slice(0, 8)}.pdf`)}
            >
              <span className="material-symbols-outlined icon-sm">picture_as_pdf</span>Download PDF
            </button>
            {can(user, "insights", "write") && (
              <button type="button" className="btn" disabled={busy} onClick={async () => { setBusy(true); try { setData(await post<Comparison>(`/api/comparisons/${comparisonId}/narrative`)); } catch (err) { setError(errorText(err)); } finally { setBusy(false); } }}>
                <span className="material-symbols-outlined icon-sm">auto_awesome</span>{busy ? "Writing…" : data.narrative ? "Regenerate narrative" : "Generate narrative"}
              </button>
            )}
            {can(user, "insights", "write") && <button type="button" className="btn btn-danger" onClick={() => setDeleting(true)}><span className="material-symbols-outlined icon-sm">delete</span></button>}
          </>
        }
      />
      <nav className="flex border-b border-line bg-surface">
        {([["report", "insights", "Report"], ["contracts", "contract_edit", "API & data contracts"], ["files", "difference", "Files & symbols"], ["chat", "forum", "Chat about this comparison"]] as const).map(([key, icon, label]) => (
          <button key={key} type="button" className={`tab ${tab === key ? "tab-active" : ""}`} onClick={() => setTab(key)}><span className="material-symbols-outlined icon-sm">{icon}</span>{label}</button>
        ))}
      </nav>
      <div className="min-h-0 flex-1 overflow-auto">
        {tab === "chat" && <div className="h-full"><ChatPanel scopeType="comparison" scopeId={comparisonId} /></div>}
        {tab !== "chat" && (
          <div className="space-y-4 p-4">
            <ErrorNote message={error} />
            <div className="grid gap-3 md:grid-cols-2">
              {([["LEFT · baseline", r.left], ["RIGHT · candidate", r.right]] as const).map(([label, side]) => (
                <div key={label} className="panel flex items-center gap-3 p-3">
                  <HealthGauge score={side.health_score} size={52} />
                  <div className="min-w-0">
                    <div className="font-mono text-micro text-faint">{label}</div>
                    <Link to={`/analyses/${side.analysis_id}`} className="font-mono text-code hover:text-primary">{side.repo_name}</Link>
                    <div className="font-mono text-code"><span className="text-primary">{side.branch || "—"}</span> <ShortSha sha={side.commit_sha} /> <span className="text-faint">{formatDateTime(side.finished_at)}</span></div>
                  </div>
                </div>
              ))}
            </div>
            {tab === "report" && (
              <>
                {data.narrative && <section className="panel p-4"><Markdown text={data.narrative} /></section>}
                <div className="grid gap-4 xl:grid-cols-2">
                  <section className="panel">
                    <div className="panel-head"><span>Metrics</span><span>dependency similarity {r.dependency_similarity_pct}% · content similarity {r.files.content_similarity_pct}%</span></div>
                    <table className="w-full">
                      <thead className="table-head"><tr><th>Metric</th><th className="text-right">Left</th><th className="text-right">Right</th><th className="text-right">Δ</th></tr></thead>
                      <tbody>
                        {r.metrics.map((m) => (
                          <tr key={m.metric} className="table-row">
                            <td className="font-mono text-code">{m.metric.replace(/_/g, " ")}</td>
                            <td className="text-right font-mono tnum">{m.left ?? "—"}</td>
                            <td className="text-right font-mono tnum">{m.right ?? "—"}</td>
                            <td className="text-right font-mono"><Delta metric={m.metric} delta={m.delta} /></td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </section>
                  <section className="panel p-3 space-y-3">
                    <div className="label-micro">Structure</div>
                    <div className="grid grid-cols-2 gap-3 font-mono text-code">
                      <div><span className="text-faint">Style L:</span> {r.structure.architecture_style.left}</div>
                      <div><span className="text-faint">Style R:</span> {r.structure.architecture_style.right}</div>
                    </div>
                    <div><div className="font-mono text-micro text-faint">Languages · left</div><LanguageBar languages={r.structure.languages.left} /></div>
                    <div><div className="font-mono text-micro text-faint">Languages · right</div><LanguageBar languages={r.structure.languages.right} /></div>
                    <table className="w-full">
                      <thead className="table-head"><tr><th>Layer</th><th className="text-right">Left</th><th className="text-right">Right</th></tr></thead>
                      <tbody>
                        {["api", "service", "data", "ui", "shared"].map((layer) => (
                          <tr key={layer} className="table-row"><td className="font-mono text-code">{layer}</td><td className="text-right font-mono tnum">{r.structure.layers.left[layer] ?? 0}</td><td className="text-right font-mono tnum">{r.structure.layers.right[layer] ?? 0}</td></tr>
                        ))}
                      </tbody>
                    </table>
                  </section>
                </div>
                <Columns title="Technology stack" left={r.stack.only_left} right={r.stack.only_right} common={r.stack.common} />
                <Columns title={`Declared dependencies (${r.dependencies.common} common)`} left={r.dependencies.only_left} right={r.dependencies.only_right} />
                {r.dependencies.version_changes.length > 0 && (
                  <section className="panel">
                    <div className="panel-head"><span>Version changes ({r.dependencies.version_changes.length})</span></div>
                    <table className="w-full"><tbody>
                      {r.dependencies.version_changes.map((v) => (
                        <tr key={v.name + v.ecosystem} className="table-row"><td className="font-mono text-code">{v.name}</td><td className="font-mono text-micro text-faint">{v.ecosystem}</td><td className="font-mono text-code text-critical">{v.left}</td><td className="font-mono text-code text-ok">{v.right}</td></tr>
                      ))}
                    </tbody></table>
                  </section>
                )}
                <div className="grid gap-4 md:grid-cols-2">
                  {([["New concerns (right)", r.concerns.new], ["Resolved concerns", r.concerns.resolved]] as const).map(([title, items]) => (
                    <section key={title} className="panel">
                      <div className="panel-head"><span>{title} · {items.length}</span><span>{r.concerns.persisting} persisting</span></div>
                      <div className="max-h-80 overflow-auto">
                        {items.map((c, i) => (
                          <div key={i} className="flex items-start gap-2 border-b border-line/60 px-3 py-1.5"><SeverityBadge severity={c.severity} /><div><div className="text-body-md">{c.title}</div><div className="font-mono text-micro text-faint">{c.category} {c.path && `· ${c.path}`}</div></div></div>
                        ))}
                      </div>
                    </section>
                  ))}
                </div>
              </>
            )}
            {tab === "contracts" && <ContractChanges catalog={r.catalog} />}
            {tab === "files" && (
              <>
                <div className="grid grid-cols-4 gap-2">
                  {(["modified", "added", "removed", "unchanged"] as const).map((key) => (
                    <button key={key} type="button" disabled={key === "unchanged"} className={`card text-left ${fileTab === key ? "border-primary" : ""}`} onClick={() => key !== "unchanged" && setFileTab(key)}>
                      <div className="label-micro">{key}</div>
                      <div className={`kpi ${key === "added" ? "text-ok" : key === "removed" ? "text-critical" : key === "modified" ? "text-warning" : ""}`}>{r.files.counts[key]}</div>
                    </button>
                  ))}
                </div>
                <section className="panel">
                  <table className="w-full">
                    <thead className="table-head"><tr><th>File</th><th>Layer</th><th>Role</th><th className="text-right">LOC</th>{fileTab === "modified" && <th className="text-right">Δ LOC · Δ McCabe</th>}</tr></thead>
                    <tbody>
                      {fileTab === "modified" && r.files.modified.map((f) => (
                        <tr key={f.path} className="table-row"><td className="font-mono text-code">{f.path}</td><td className="font-mono text-micro">{f.layer}</td><td className="text-body-sm">{f.role}</td><td className="text-right font-mono tnum">{f.loc_left} → {f.loc_right}</td><td className="text-right font-mono tnum"><Delta metric="loc" delta={f.delta} /> · <Delta metric="avg_complexity" delta={f.complexity_delta} /></td></tr>
                      ))}
                      {fileTab !== "modified" && r.files[fileTab].map((f) => (
                        <tr key={f.path} className="table-row"><td className={`font-mono text-code ${fileTab === "added" ? "text-ok" : "text-critical"}`}>{fileTab === "added" ? "+ " : "− "}{f.path}</td><td className="font-mono text-micro">{f.layer}</td><td className="text-body-sm">{f.role}</td><td className="text-right font-mono tnum">{f.loc}</td></tr>
                      ))}
                    </tbody>
                  </table>
                </section>
                {r.symbols.added_count !== undefined && (
                  <div className="grid gap-4 md:grid-cols-2">
                    {([["Public symbols added", r.symbols.added ?? [], r.symbols.added_count, "text-ok"], ["Public symbols removed", r.symbols.removed ?? [], r.symbols.removed_count, "text-critical"]] as const).map(([title, items, count, cls]) => (
                      <section key={title} className="panel">
                        <div className="panel-head"><span>{title}</span><span>{count}</span></div>
                        <ul className="max-h-80 overflow-auto p-2 font-mono text-micro">{items.map((s) => <li key={s} className={cls}>{s}</li>)}</ul>
                      </section>
                    ))}
                  </div>
                )}
              </>
            )}
          </div>
        )}
      </div>
      {deleting && <ConfirmDialog title="Delete comparison" message="Delete this comparison and its chats?" onClose={() => setDeleting(false)} onConfirm={async () => { await del(`/api/comparisons/${comparisonId}`); navigate("/compare"); }} />}
    </div>
  );
}


const CONTRACT_KINDS: [string, string][] = [["endpoint", "API endpoints"], ["table", "Tables & columns"], ["screen", "Screens"], ["api_client", "Client calls"], ["job", "Scheduled jobs"], ["config", "Configuration keys"]];

function ContractChanges({ catalog }: { catalog?: CatalogDiff }) {
  if (!catalog?.available) {
    return <section className="panel p-4 text-body-md text-muted">One of these analyses has no layer catalogue. Open each analysis › Layer Catalogue and build it, then refresh this comparison.</section>;
  }
  return (
    <>
      <section className={`panel ${catalog.breaking.length ? "border-critical/50" : ""}`}>
        <div className="panel-head"><span className="flex items-center gap-1"><span className={`material-symbols-outlined icon-sm ${catalog.breaking.length ? "text-critical" : "text-ok"}`}>{catalog.breaking.length ? "warning" : "verified"}</span>Potentially breaking changes</span><span>{catalog.breaking.length}</span></div>
        {catalog.breaking.length ? <ul className="space-y-0.5 p-3">{catalog.breaking.map((b) => <li key={b} className="font-mono text-code text-critical">• {b}</li>)}</ul> : <p className="p-3 text-body-md text-ok">No removed endpoints / tables, new required fields, removed response fields or column type changes.</p>}
      </section>
      <div className="grid gap-4 xl:grid-cols-2">
        {CONTRACT_KINDS.map(([kind, title]) => {
          const part = catalog[kind] as CatalogDiffPart | undefined;
          if (!part || (!part.added.length && !part.removed.length && !part.changed.length)) return null;
          return (
            <section key={kind} className="panel">
              <div className="panel-head"><span>{title}</span><span className="tnum"><span className="text-ok">+{part.added.length}</span> <span className="text-critical">−{part.removed.length}</span> <span className="text-warning">~{part.changed.length}</span></span></div>
              <div className="max-h-96 space-y-0.5 overflow-auto p-3 font-mono text-code">
                {part.added.map((n) => <div key={`a${n}`} className="text-ok">+ {n}</div>)}
                {part.removed.map((n) => <div key={`r${n}`} className="text-critical">− {n}</div>)}
                {part.changed.map((c) => (
                  <div key={`c${c.name}`} className="pt-1">
                    <div className="text-warning">~ {c.name}</div>
                    <ul className="ml-4 text-micro">{c.changes.map((x, i) => <li key={i} className={x.breaking ? "text-critical" : "text-muted"}>{x.breaking ? "⚠ " : "· "}{x.text}</li>)}</ul>
                  </div>
                ))}
              </div>
            </section>
          );
        })}
      </div>
    </>
  );
}
