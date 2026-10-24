import { Fragment, useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, del, errorText, formatDateTime, post } from "../../api";
import { useAuth } from "../../auth";
import { DependencyGraph, EDGE_KINDS } from "../../components/DependencyGraph";
import { ConfirmDialog, Empty, ErrorNote, LayerPill, Markdown, Meter, PageHeader, SeverityBadge, ShortSha, Spinner, StatusBadge } from "../../components/ui";
import { can } from "../../permissions";
import type { ChangedSymbol, ConfigKeyChange, EntryPoint, FileDiffPayload, GraphEdge, GraphNode, ImpactEvent, ImpactRun, ImpactedFile } from "../../types";
import { ChangeChip, FindingSeverity, FlagChip, KindChips, ReachChip, RiskBadge, RiskGauge, Stat, basename } from "./shared";

type Tab = "summary" | "changes" | "features" | "config" | "tests" | "graph" | "reviewers";
const TABS: [Tab, string, string][] = [
  ["summary", "insights", "Summary"],
  ["changes", "difference", "Changes & logic"],
  ["features", "category", "Features & entry points"],
  ["config", "tune", "Configuration"],
  ["tests", "science", "Tests"],
  ["graph", "hub", "Impact graph"],
  ["reviewers", "group", "Reviewers"],
];

export function ImpactRunPage() {
  const { runId = "" } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [run, setRun] = useState<ImpactRun | null>(null);
  const [events, setEvents] = useState<ImpactEvent[]>([]);
  const [tab, setTab] = useState<Tab>("summary");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState("");
  const [deleting, setDeleting] = useState(false);
  const active = run?.status === "queued" || run?.status === "running";
  const canWrite = can(user, "repositories", "write");

  const load = useCallback(() => {
    api<ImpactRun>(`/api/impact-runs/${runId}`).then(setRun).catch((err) => setError(errorText(err)));
    api<ImpactEvent[]>(`/api/impact-runs/${runId}/events`).then(setEvents).catch(() => undefined);
  }, [runId]);
  useEffect(() => load(), [load]);
  useEffect(() => {
    if (!active) return;
    const timer = setInterval(load, 2000);
    return () => clearInterval(timer);
  }, [active, load]);

  async function action(key: string, fn: () => Promise<void>) {
    setBusy(key);
    setError("");
    try {
      await fn();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy("");
    }
  }

  if (!run) return <div className="p-6">{error ? <ErrorNote message={error} /> : <Spinner label="Loading impact run…" />}</div>;
  const r = run.result;
  const completed = run.status === "completed" && r?.risk;

  return (
    <div className="flex h-full flex-col">
      <PageHeader
        icon="difference"
        title={<span className="font-mono">{run.title || `${run.head_ref} → ${run.base_ref}`}</span>}
        subtitle={
          <span className="flex flex-wrap items-center gap-3 font-mono text-code">
            <Link to={`/repositories/${run.repo_id}`} className="hover:text-primary">{run.repo?.name}</Link>
            <span><span className="text-primary">{run.head_ref}</span> → {run.base_ref}</span>
            <span className="text-faint">merge-base <ShortSha sha={run.merge_base_sha} /> · head <ShortSha sha={run.head_sha} /></span>
            {run.pull_request?.url && <a className="link" href={run.pull_request.url} target="_blank" rel="noreferrer">open PR #{run.pull_request.number}</a>}
            <span className="text-faint">by {run.started_by_name} · {formatDateTime(run.created_at)}</span>
          </span>
        }
        right={
          <>
            {completed && <RiskBadge label={run.risk_label} score={run.risk} />}
            <StatusBadge status={run.status} progress={run.progress} />
            {run.base_analysis_id && <Link className="btn" to={`/analyses/${run.base_analysis_id}`} title="Analysis of the merge-base">base</Link>}
            {run.head_analysis_id && <Link className="btn" to={`/analyses/${run.head_analysis_id}`} title="Analysis of the head">head</Link>}
            {canWrite && !active && (
              <button type="button" className="btn" disabled={!!busy} onClick={() => void action("rerun", async () => { const next = await post<ImpactRun>(`/api/impact-runs/${runId}/rerun`); navigate(`/impact/${next.run_id}`); })}>
                <span className="material-symbols-outlined icon-sm">replay</span>Re-run
              </button>
            )}
            {canWrite && completed && (
              <button type="button" className="btn" disabled={!!busy} onClick={() => void action("story", async () => setRun(await post<ImpactRun>(`/api/impact-runs/${runId}/narrative`)))}>
                <span className="material-symbols-outlined icon-sm">auto_awesome</span>{busy === "story" ? "Writing…" : "Regenerate narrative"}
              </button>
            )}
            {canWrite && active && (
              <button type="button" className="btn btn-danger" onClick={() => void action("cancel", async () => { await post(`/api/impact-runs/${runId}/cancel`); load(); })}>Cancel</button>
            )}
            {canWrite && <button type="button" className="btn btn-danger" title="Delete run" onClick={() => setDeleting(true)}><span className="material-symbols-outlined icon-sm">delete</span></button>}
          </>
        }
      />
      {completed && (
        <nav className="flex overflow-x-auto border-b border-line bg-surface">
          {TABS.map(([key, icon, label]) => (
            <button key={key} type="button" className={`tab whitespace-nowrap ${tab === key ? "tab-active" : ""}`} onClick={() => setTab(key)}>
              <span className="material-symbols-outlined icon-sm">{icon}</span>{label}
            </button>
          ))}
        </nav>
      )}
      <div className="min-h-0 flex-1 overflow-auto">
        <div className="space-y-4 p-4">
          <ErrorNote message={error} />
          {!completed && <Progress run={run} events={events} />}
          {completed && tab === "summary" && <SummaryTab run={run} />}
          {completed && tab === "changes" && <ChangesTab run={run} />}
          {completed && tab === "features" && <FeaturesTab run={run} />}
          {completed && tab === "config" && <ConfigTab run={run} />}
          {completed && tab === "tests" && <TestsTab run={run} />}
          {completed && tab === "graph" && <GraphTab run={run} />}
          {completed && tab === "reviewers" && <ReviewersTab run={run} />}
        </div>
      </div>
      {deleting && (
        <ConfirmDialog title="Delete impact run" message="Delete this run? Its base / head analyses are deleted too when no other run uses them." onClose={() => setDeleting(false)}
          onConfirm={async () => { await del(`/api/impact-runs/${runId}`); navigate(`/impact?repo=${run.repo_id}`); }} />
      )}
    </div>
  );
}

function Progress({ run, events }: { run: ImpactRun; events: ImpactEvent[] }) {
  return (
    <section className="panel">
      <div className="panel-head"><span>{run.stage || run.status}</span><span className="tnum">{Math.round(run.progress)}%</span></div>
      <div className="space-y-2 p-3">
        <div className="h-1.5 w-full bg-elevated"><div className={`h-full bg-primary ${run.status === "running" ? "stripes" : ""}`} style={{ width: `${run.progress}%` }} /></div>
        {run.error && <ErrorNote message={run.error.split("\n")[0]} />}
        <div className="max-h-[50vh] overflow-auto bg-inset p-2 font-mono text-micro">
          {events.map((e) => (
            <div key={e.event_id} className={e.level === "error" ? "text-critical" : e.level === "warn" ? "text-warning" : "text-muted"}>
              <span className="text-faint">{formatDateTime(e.ts).slice(-5)} </span>{e.message}
            </div>
          ))}
          {!events.length && <span className="text-faint">Waiting for a worker…</span>}
        </div>
      </div>
    </section>
  );
}

// --- summary ------------------------------------------------------------------------------------------------------

function SummaryTab({ run }: { run: ImpactRun }) {
  const r = run.result;
  const n = run.narrative ?? {};
  const strong = r.impacted.filter((i) => i.confidence !== "low" && !i.is_test).length;
  const contract = run.symbols.filter((s) => s.flags.includes("contract")).length;
  const severe = r.config.findings.filter((f) => f.severity === "critical" || f.severity === "high").length;
  return (
    <>
      <div className="grid gap-4 xl:grid-cols-[300px_1fr]">
        <section className="panel flex flex-col items-center justify-center gap-2 p-4">
          <RiskGauge score={r.risk.score} label={r.risk.label} size={180} />
          {r.risk.overrides.length > 0 && (
            <div className="w-full border border-critical/40 bg-critical/10 p-2 text-body-sm text-critical">
              <div className="label-micro text-critical">Raised to at least HIGH because</div>
              <ul className="mt-1 list-disc pl-4">{r.risk.overrides.map((o) => <li key={o}>{o}</li>)}</ul>
            </div>
          )}
          <p className="text-center font-mono text-micro text-faint">deterministic score · ruleset v{run.ruleset_version} · the LLM never sets the label</p>
        </section>
        <div className="grid grid-cols-2 gap-2 md:grid-cols-3 xl:grid-cols-6">
          <Stat label="Files changed" value={r.stats.files} sub={<><span className="text-ok">+{r.stats.additions}</span> <span className="text-critical">−{r.stats.deletions}</span></>} />
          <Stat label="Symbols changed" value={r.stats.symbols} sub={`${contract} public contract change(s)`} tone={contract ? "text-warning" : ""} />
          <Stat label="Files impacted" value={strong} sub={`${r.impacted.length - strong} weak / test links`} />
          <Stat label="Entry points" value={r.entry_points.length} sub={`${r.entry_points.filter((e) => ["direct", "same file", "removed"].includes(e.reach)).length} direct or removed`} />
          <Stat label="Config findings" value={r.config.findings.length} sub={`${severe} high or critical`} tone={severe ? "text-critical" : ""} />
          <Stat label="Test gaps" value={r.tests.gaps.length} sub={`of ${r.tests.functions} changed function(s)`} tone={r.tests.gaps.length ? "text-warning" : "text-ok"} />
          <section className="panel col-span-full">
            <div className="panel-head"><span>Why this risk · factor contributions</span><span>Σ {r.risk.score.toFixed(3)}</span></div>
            <table className="w-full">
              <tbody>
                {r.risk.factors.map((f) => (
                  <tr key={f.factor} className="table-row">
                    <td className="w-40 font-mono text-code">{f.factor.replace(/_/g, " ")}</td>
                    <td className="w-48"><Meter value={f.value} max={1} color={f.value >= 0.7 ? "rgb(var(--critical))" : f.value >= 0.4 ? "rgb(var(--warning))" : "rgb(var(--primary))"} /></td>
                    <td className="w-28 text-right font-mono text-micro tnum text-muted">{f.value.toFixed(2)} × {f.weight}</td>
                    <td className="w-16 text-right font-mono text-code tnum">{f.contribution.toFixed(3)}</td>
                    <td className="text-body-sm text-muted">{f.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        </div>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <section className="panel">
          <div className="panel-head"><span>What this change does</span><span className={n.source === "llm" ? "text-primary" : ""}>{n.source === "llm" ? "LLM narrative over deterministic facts" : "deterministic summary"}</span></div>
          <div className="space-y-3 p-3">
            {n.summary && <Markdown text={n.summary} />}
            {n.risk_explanation && <p className="text-body-md text-muted"><span className="label-micro">Risk · </span>{n.risk_explanation}</p>}
            {run.pull_request?.description && (
              <details className="text-body-sm">
                <summary className="cursor-pointer label-micro">PR description</summary>
                <pre className="mt-1 max-h-60 overflow-auto whitespace-pre-wrap bg-inset p-2 font-mono text-micro text-muted">{run.pull_request.description}</pre>
              </details>
            )}
          </div>
        </section>
        <section className="panel">
          <div className="panel-head"><span>Review checklist</span><span>{n.review_checklist?.length ?? 0}</span></div>
          <ul className="space-y-1 p-3">
            {(n.review_checklist ?? []).map((item) => (
              <li key={item} className="flex items-start gap-2 text-body-md"><span className="material-symbols-outlined icon-sm text-primary">check_box_outline_blank</span>{item}</li>
            ))}
            {!n.review_checklist?.length && <li className="text-muted">Nothing specific to check.</li>}
          </ul>
          {!!n.description_drift?.length && (
            <div className="border-t border-line p-3">
              <div className="label-micro text-warning">Description vs diff</div>
              <ul className="mt-1 space-y-1">{n.description_drift.map((d) => <li key={d} className="flex items-start gap-2 text-body-md text-warning"><span className="material-symbols-outlined icon-sm">report</span>{d}</li>)}</ul>
            </div>
          )}
        </section>
      </div>
      <section className="panel p-3">
        <div className="label-micro">Limits of this analysis</div>
        <ul className="mt-1 list-disc space-y-0.5 pl-5 text-body-sm text-muted">
          {r.limits.map((l) => <li key={l}>{l}</li>)}
          {r.stats.history_shallow && <li>History was fetched shallowly; churn and author statistics are approximate.</li>}
          <li>The PR head is never built, installed or run: all results are static.</li>
        </ul>
      </section>
    </>
  );
}

// --- changes ------------------------------------------------------------------------------------------------------

function ChangesTab({ run }: { run: ImpactRun }) {
  const r = run.result;
  const [open, setOpen] = useState<string | null>(null);
  const [diff, setDiff] = useState<FileDiffPayload | null>(null);
  const [onlyCode, setOnlyCode] = useState(false);
  const byFile = useMemo(() => {
    const map: Record<string, ChangedSymbol[]> = {};
    for (const s of run.symbols) (map[s.path] ??= []).push(s);
    return map;
  }, [run.symbols]);
  useEffect(() => {
    setDiff(null);
    if (open) api<FileDiffPayload>(`/api/impact-runs/${run.run_id}/diff?path=${encodeURIComponent(open)}`).then(setDiff).catch(() => undefined);
  }, [open, run.run_id]);
  const files = r.files.filter((f) => !onlyCode || f.change_kinds.includes("code"));
  return (
    <section className="panel">
      <div className="panel-head">
        <span>Changed files · {r.files.length}</span>
        <label className="flex items-center gap-1 normal-case"><input type="checkbox" checked={onlyCode} onChange={(e) => setOnlyCode(e.target.checked)} />code only</label>
      </div>
      <table className="w-full">
        <thead className="table-head"><tr><th /><th>File</th><th>Change</th><th>Kinds</th><th>Layer</th><th className="text-right">+/−</th><th>Symbols</th></tr></thead>
        <tbody>
          {files.map((f) => {
            const symbols = byFile[f.path] ?? [];
            const isOpen = open === f.path;
            return (
              <Fragment key={f.path}>
                <tr className="table-row cursor-pointer" onClick={() => setOpen(isOpen ? null : f.path)}>
                  <td className="w-6"><span className="material-symbols-outlined icon-sm text-faint">{isOpen ? "expand_more" : "chevron_right"}</span></td>
                  <td className="font-mono text-code">
                    {f.old_path && f.status === "R" && <span className="text-faint">{f.old_path} → </span>}{f.path}
                    {f.sensitive && <span className="chip ml-1 border-critical/40 text-critical">sensitive</span>}
                    {f.tags_added.map((t) => <span key={t} className="chip ml-1 border-warning/50 normal-case text-warning">+{t}</span>)}
                  </td>
                  <td><ChangeChip change={f.status} /></td>
                  <td><KindChips kinds={f.change_kinds} /></td>
                  <td>{f.layer && <LayerPill layer={f.layer} />}</td>
                  <td className="text-right font-mono text-micro tnum"><span className="text-ok">+{f.additions}</span> <span className="text-critical">−{f.deletions}</span></td>
                  <td className="font-mono text-micro text-muted">{symbols.length || ""}</td>
                </tr>
                {isOpen && (
                  <tr>
                    <td colSpan={7} className="border-b border-line bg-inset p-3">
                      <div className="grid gap-3 2xl:grid-cols-2">
                        <div className="space-y-2">
                          {symbols.map((s) => <SymbolCard key={`${s.qualified_name}${s.change_type}${s.line_start}`} symbol={s} />)}
                          {!symbols.length && <p className="text-body-sm text-muted">No symbol-level changes (configuration, assets, or module-level code).</p>}
                        </div>
                        <DiffView diff={diff} />
                      </div>
                    </td>
                  </tr>
                )}
              </Fragment>
            );
          })}
        </tbody>
      </table>
    </section>
  );
}

function SymbolCard({ symbol: s }: { symbol: ChangedSymbol }) {
  const logic = s.logic ?? {};
  return (
    <div className="border border-line bg-surface p-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <ChangeChip change={s.change_type} />
        <span className="font-mono text-code text-ink">{s.qualified_name}</span>
        <span className="font-mono text-micro text-faint">{s.kind}{s.line_start ? ` · L${s.line_start}–${s.line_end}` : ""}</span>
        {s.flags.map((f) => <FlagChip key={f} flag={f} />)}
      </div>
      {s.flags.includes("signature") && (
        <div className="mt-1 font-mono text-micro">
          <div className="text-critical">− {s.signature_before}</div>
          <div className="text-ok">+ {s.signature_after}</div>
        </div>
      )}
      {logic.summary && (
        <div className="mt-2 border-l-2 border-primary pl-2">
          <div className="flex items-center gap-1 label-micro text-primary"><span className="material-symbols-outlined icon-sm">auto_awesome</span>Changed logic{s.logic_source === "cache" ? " · reused" : ""}</div>
          <p className="text-body-md">{logic.summary}</p>
          {!!logic.rules_changed?.length && (
            <table className="mt-1 w-full text-body-sm">
              <thead className="table-head"><tr><th>Before</th><th>After</th><th>Line</th></tr></thead>
              <tbody>{logic.rules_changed.map((rule, i) => <tr key={i} className="table-row"><td className="text-critical">{rule.before}</td><td className="text-ok">{rule.after}</td><td className="font-mono text-micro">{rule.line || "—"}</td></tr>)}</tbody>
            </table>
          )}
          {!!logic.side_effects?.length && (
            <div className="mt-1 flex flex-wrap gap-1">
              {logic.side_effects.map((e, i) => (
                <span key={i} className={`chip normal-case ${e.verified === "yes" ? "border-ok/50 text-ok" : e.verified === "unverified" ? "border-warning/50 text-warning" : "border-line text-muted"}`} title={e.detail}>
                  {e.kind}{e.verified === "yes" ? " ✓ verified" : e.verified === "unverified" ? " ? unverified" : ""}
                </span>
              ))}
            </div>
          )}
          {logic.inputs_outputs && <p className="mt-1 text-body-sm text-muted"><span className="label-micro">In / out · </span>{logic.inputs_outputs}</p>}
          {logic.error_handling && <p className="text-body-sm text-muted"><span className="label-micro">Errors · </span>{logic.error_handling}</p>}
          {!!logic.test_focus?.length && <p className="text-body-sm text-muted"><span className="label-micro">Test focus · </span>{logic.test_focus.join(" · ")}</p>}
          {!!logic.risk_notes?.length && <p className="text-body-sm text-warning"><span className="label-micro text-warning">Risk notes · </span>{logic.risk_notes.join(" · ")}</p>}
        </div>
      )}
    </div>
  );
}

function DiffView({ diff }: { diff: FileDiffPayload | null }) {
  if (!diff) return <div className="p-2"><Spinner label="Loading diff…" /></div>;
  if (!diff.hunks.length) return <p className="text-body-sm text-muted">No textual hunks (binary or rename only).</p>;
  return (
    <div className="max-h-[60vh] overflow-auto border border-line bg-canvas font-mono text-micro">
      {diff.hunks.map((h, i) => (
        <div key={i} className="border-b border-line/60">
          <div className="bg-elevated px-2 py-0.5 text-faint">@@ −{h.old_start},{h.old_count} +{h.new_start},{h.new_count} @@ {h.header}</div>
          {h.removed.map(([line, text]) => <div key={`r${line}`} className="whitespace-pre bg-critical/10 px-2 text-critical"><span className="mr-2 inline-block w-10 text-right text-faint">{line}</span>−{text}</div>)}
          {h.added.map(([line, text]) => <div key={`a${line}`} className="whitespace-pre bg-ok/10 px-2 text-ok"><span className="mr-2 inline-block w-10 text-right text-faint">{line}</span>+{text}</div>)}
        </div>
      ))}
    </div>
  );
}

// --- features & entry points --------------------------------------------------------------------------------------

function FeaturesTab({ run }: { run: ImpactRun }) {
  const r = run.result;
  const names = useMemo(() => Object.fromEntries(r.features.map((f) => [f.feature_id, f.name])), [r.features]);
  const groups = useMemo(() => {
    const map: Record<string, EntryPoint[]> = {};
    for (const e of r.entry_points) {
      const ids = e.feature_ids?.length ? e.feature_ids : ["_none"];
      for (const id of ids) (map[id] ??= []).push(e);
    }
    return Object.entries(map).sort(([a], [b]) => (a === "_none" ? 1 : b === "_none" ? -1 : (names[a] ?? "").localeCompare(names[b] ?? "")));
  }, [r.entry_points, names]);
  const summaries = Object.fromEntries((run.narrative.features ?? []).map((f) => [f.feature, f.summary]));
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-2">
        <p className="text-body-md text-muted">
          Entry points are reached from the changed code through symbol-level usage, the same file, or catalogue links (a screen that calls an endpoint).
          {!r.features.length && " No feature catalogue yet — create one to see the impact per business capability."}
        </p>
        <Link className="btn" to={`/repositories/${run.repo_id}/features`}><span className="material-symbols-outlined icon-sm">category</span>Edit feature catalogue</Link>
      </div>
      {!r.entry_points.length && <section className="panel"><Empty icon="alt_route" title="No entry points affected">No endpoint, job, listener, screen or main program is reached by this change.</Empty></section>}
      {groups.map(([id, entries]) => {
        const name = id === "_none" ? (r.features.length ? "Not in any feature" : "Entry points") : names[id] ?? id;
        const confirmed = r.features.find((f) => f.feature_id === id)?.confirmed;
        return (
          <section key={id} className="panel">
            <div className="panel-head">
              <span className="flex items-center gap-2">{name}{id !== "_none" && !confirmed && <span className="chip border-warning/50 text-warning">unconfirmed</span>}</span>
              <span>{entries.length} entry point(s)</span>
            </div>
            {summaries[name] && <p className="border-b border-line px-3 py-2 text-body-md"><span className="material-symbols-outlined icon-sm mr-1 align-middle text-primary">auto_awesome</span>{summaries[name]}</p>}
            <table className="w-full">
              <thead className="table-head"><tr><th>Reach</th><th>Kind</th><th>Entry point</th><th>Location</th><th>Via</th><th>Contract changes</th></tr></thead>
              <tbody>
                {entries.map((e) => (
                  <tr key={e.item_key} className="table-row">
                    <td><ReachChip reach={e.reach} />{e.depth > 0 && <span className="ml-1 font-mono text-micro text-faint">d{e.depth}</span>}</td>
                    <td className="font-mono text-micro">{e.kind}</td>
                    <td className="font-mono text-code">{e.name}{e.framework && <div className="text-micro text-faint">{e.framework}</div>}</td>
                    <td className="break-all font-mono text-micro text-muted">{e.path}{e.line ? `:${e.line}` : ""}</td>
                    <td className="font-mono text-micro text-muted">{e.via && e.via !== e.path ? basename(e.via) : ""}{e.symbols.length > 0 && <div className="text-faint">uses {e.symbols.slice(0, 4).join(", ")}</div>}</td>
                    <td className="min-w-[260px]">
                      {e.contract_changes?.slice(0, 4).map((c, i) => <div key={i} className={`font-mono text-micro ${c.breaking ? "text-critical" : "text-muted"}`}>{c.breaking ? "⚠ " : "· "}{c.text}</div>)}
                      {(e.contract_changes?.length ?? 0) > 4 && <div className="font-mono text-micro text-faint">+{(e.contract_changes?.length ?? 0) - 4} more</div>}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </section>
        );
      })}
    </>
  );
}

// --- configuration ------------------------------------------------------------------------------------------------

function ConfigTab({ run }: { run: ImpactRun }) {
  const c = run.result.config;
  const [env, setEnv] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const envs = Object.entries(c.environments).sort(([a], [b]) => (a === "prod" ? -1 : b === "prod" ? 1 : a.localeCompare(b)));
  const keys = c.keys.filter((k) => !env || k.environment === env);
  const infra = [...c.infra, ...c.ci];
  if (!c.files.length && !c.findings.length) return <section className="panel"><Empty icon="tune" title="No configuration, infrastructure or CI changes" /></section>;
  return (
    <>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-6">
        <button type="button" className={`card text-left ${env === "" ? "border-primary" : ""}`} onClick={() => setEnv("")}>
          <div className="label-micro">All environments</div><div className="kpi">{c.keys.length}</div><div className="font-mono text-micro text-faint">key change(s)</div>
        </button>
        {envs.map(([name, e]) => (
          <button key={name} type="button" className={`card text-left ${env === name ? "border-primary" : ""}`} onClick={() => setEnv(name)}>
            <div className={`label-micro ${name === "prod" ? "text-critical" : ""}`}>{name === "default" ? "default (all, unless overridden)" : name}</div>
            <div className="kpi">{e.added + e.removed + e.changed}</div>
            <div className="font-mono text-micro text-faint"><span className="text-ok">+{e.added}</span> <span className="text-critical">−{e.removed}</span> <span className="text-warning">~{e.changed}</span>{e.findings ? ` · ${e.findings} finding(s)` : ""}</div>
          </button>
        ))}
      </div>
      {!!c.findings.length && (
        <section className="panel">
          <div className="panel-head"><span>Findings</span><span>{c.findings.length}</span></div>
          <div className="max-h-96 overflow-auto">
            {c.findings.filter((f) => !env || f.environment === env).map((f, i) => (
              <div key={i} className="flex items-start gap-2 border-b border-line/60 px-3 py-1.5">
                <FindingSeverity severity={f.severity} />
                <div className="min-w-0">
                  <div className="text-body-md">{f.message}</div>
                  <div className="font-mono text-micro text-faint">{f.rule}{f.environment ? ` · ${f.environment}` : ""}{f.path ? ` · ${f.path}${f.line ? `:${f.line}` : ""}` : ""}</div>
                </div>
              </div>
            ))}
          </div>
        </section>
      )}
      {!!keys.length && (
        <section className="panel">
          <div className="panel-head"><span>Keys changed{env ? ` · ${env}` : ""}</span><span>{keys.length}</span></div>
          <table className="w-full">
            <thead className="table-head"><tr><th>Key</th><th>Env</th><th>Change</th><th>Before → after</th><th>Read by</th><th>Consumer</th></tr></thead>
            <tbody>
              {keys.map((k: ConfigKeyChange) => {
                const id = `${k.key}|${k.environment}|${k.path}`;
                return (
                  <Fragment key={id}>
                    <tr className="table-row cursor-pointer" onClick={() => setOpen(open === id ? null : id)}>
                      <td className="font-mono text-code">{k.key}{k.secret && <span className="chip ml-1 border-critical/40 text-critical">secret</span>}<div className="text-micro text-faint">{k.path}{k.line ? `:${k.line}` : ""}</div></td>
                      <td className={`font-mono text-micro ${k.environment === "prod" ? "text-critical" : ""}`}>{k.environment}</td>
                      <td><ChangeChip change={k.change} /></td>
                      <td className="max-w-md break-all font-mono text-micro"><span className="text-critical">{k.before ?? "—"}</span> → <span className="text-ok">{k.after ?? "—"}</span></td>
                      <td className="font-mono text-micro">{k.readers.length ? `${k.readers.length} location(s)` : <span className="text-faint">none found</span>}</td>
                      <td className="text-body-sm text-muted">{k.consumer || k.category}{k.findings.map((f) => <span key={f.rule} className="ml-1"><FindingSeverity severity={f.severity} /></span>)}</td>
                    </tr>
                    {open === id && (
                      <tr><td colSpan={6} className="border-b border-line bg-inset px-3 py-2 font-mono text-micro">
                        {k.readers.map((rd, i) => <div key={i}>{rd.path}:{rd.line} <span className="text-faint">· {rd.via}</span></div>)}
                        {!k.readers.length && <span className="text-faint">No code in this repository reads this key{k.consumer ? `; it is consumed by ${k.consumer}` : ""}.</span>}
                      </td></tr>
                    )}
                  </Fragment>
                );
              })}
            </tbody>
          </table>
        </section>
      )}
      {!!infra.length && (
        <section className="panel">
          <div className="panel-head"><span>Infrastructure & CI</span><span>{infra.length}</span></div>
          {infra.map((f, i) => (
            <div key={i} className="flex items-start gap-2 border-b border-line/60 px-3 py-1.5"><FindingSeverity severity={f.severity} /><div><div className="text-body-md">{f.message}</div><div className="font-mono text-micro text-faint">{f.rule} · {f.path}</div></div></div>
          ))}
        </section>
      )}
      <section className="panel">
        <div className="panel-head"><span>Changed config, infra and CI files</span><span>{c.files.length}</span></div>
        <table className="w-full"><tbody>
          {c.files.map((f) => <tr key={f.path} className="table-row"><td className="font-mono text-code">{f.path}</td><td><ChangeChip change={f.status} /></td><td className="text-body-sm">{f.kind}</td><td className="font-mono text-micro">{f.environment}</td></tr>)}
        </tbody></table>
        {!!c.external_config.length && <p className="border-t border-line px-3 py-2 text-body-sm text-warning">External config not analysed: {c.external_config.join(", ")}.</p>}
      </section>
    </>
  );
}

// --- tests --------------------------------------------------------------------------------------------------------

function TestsTab({ run }: { run: ImpactRun }) {
  const t = run.result.tests;
  return (
    <>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        <Stat label="Affected tests" value={t.tests.length} />
        <Stat label="Covered by name" value={t.covered.length} tone="text-ok" sub="a test reaches and names the function" />
        <Stat label="Reached indirectly" value={t.indirect.length} sub="a test reaches the file only" />
        <Stat label="Gaps" value={t.gaps.length} tone={t.gaps.length ? "text-warning" : "text-ok"} sub={`gap ratio ${(t.gap_ratio * 100).toFixed(0)}%`} />
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <section className="panel">
          <div className="panel-head"><span>Gaps · changed functions no test reaches</span><span>{t.gaps.length}</span></div>
          {t.gaps.map((g) => <div key={g.symbol + g.path} className="border-b border-line/60 px-3 py-1.5 font-mono text-code"><ChangeChip change={g.change_type} /> {g.symbol} <span className="text-micro text-faint">{g.path}</span></div>)}
          {!t.gaps.length && <p className="p-3 text-body-md text-ok">Every changed function is reached by at least one test.</p>}
        </section>
        <section className="panel">
          <div className="panel-head"><span>Tests to run</span><span>{t.tests.length}</span></div>
          {t.tests.map((x) => <div key={x.path} className="border-b border-line/60 px-3 py-1.5"><div className="font-mono text-code">{x.path}</div><div className="text-body-sm text-muted">{x.reason}</div></div>)}
        </section>
      </div>
      {[["Covered by name", t.covered], ["Reached indirectly", t.indirect]].map(([title, items]) => (
        <section key={title as string} className="panel">
          <div className="panel-head"><span>{title as string}</span><span>{(items as typeof t.covered).length}</span></div>
          <table className="w-full"><tbody>
            {(items as typeof t.covered).map((c) => <tr key={c.symbol + c.path} className="table-row"><td className="font-mono text-code">{c.symbol}</td><td className="font-mono text-micro text-muted">{c.tests.join(", ")}</td></tr>)}
          </tbody></table>
        </section>
      ))}
    </>
  );
}

// --- graph --------------------------------------------------------------------------------------------------------

function GraphTab({ run }: { run: ImpactRun }) {
  const r = run.result;
  const [graph, setGraph] = useState<{ nodes: (GraphNode & { impact: "changed" | "impacted"; depth: number })[]; edges: GraphEdge[]; truncated: boolean } | null>(null);
  const [selected, setSelected] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<typeof graph>(`/api/impact-runs/${run.run_id}/graph`).then(setGraph).catch((err) => setError(errorText(err)));
  }, [run.run_id]);
  const marks = useMemo(() => Object.fromEntries((graph?.nodes ?? []).map((n) => [n.path, n.impact])), [graph]);
  const record: ImpactedFile | undefined = r.impacted.find((i) => i.path === selected);
  const kinds = useMemo(() => new Set<string>(EDGE_KINDS), []);
  return (
    <>
      <ErrorNote message={error} />
      <section className="panel">
        <div className="panel-head">
          <span>Changed (solid ring) and impacted (dashed ring) files · edges of the head analysis</span>
          <span>{graph?.nodes.length ?? 0} nodes{graph?.truncated ? " · truncated" : ""}</span>
        </div>
        <div className="grid xl:grid-cols-[1fr_340px]">
          <div className="h-[520px] border-r border-line">
            {graph ? (graph.nodes.length ? <DependencyGraph nodes={graph.nodes} edges={graph.edges} selected={selected} onSelect={setSelected} kinds={kinds} marks={marks} /> : <Empty icon="hub" title="No graph nodes" />) : <div className="p-3"><Spinner /></div>}
          </div>
          <div className="space-y-2 p-3 text-body-md">
            {!selected && <p className="text-muted">Select a node to see why it is impacted.</p>}
            {selected && (
              <>
                <div className="font-mono text-code text-ink">{selected}</div>
                {marks[selected] === "changed" && <p className="text-warning">Changed in this pull request.</p>}
                {record && (
                  <div className="space-y-1">
                    <div><span className="label-micro">Depth</span> <span className="font-mono">{record.depth}</span> · <span className="label-micro">confidence</span> <span className="font-mono">{record.confidence}</span></div>
                    <div className="text-muted">{record.reason}</div>
                    {record.via_path && <div className="font-mono text-micro">via {record.via_path} <span className="text-faint">({record.via_kind})</span></div>}
                  </div>
                )}
                {run.head_analysis_id && <Link className="link font-mono text-micro" to={`/analyses/${run.head_analysis_id}?tab=inspector&file=${encodeURIComponent(selected)}`}>Open in the inspector</Link>}
              </>
            )}
          </div>
        </div>
      </section>
      <div className="grid gap-4 xl:grid-cols-2">
        <section className="panel">
          <div className="panel-head"><span>Impacted files</span><span>{r.impacted.length}</span></div>
          <div className="max-h-96 overflow-auto">
            <table className="w-full">
              <thead className="table-head"><tr><th>File</th><th>Depth</th><th>Confidence</th><th>Why</th></tr></thead>
              <tbody>
                {r.impacted.map((i) => (
                  <tr key={i.path} className={`table-row cursor-pointer ${i.confidence === "low" ? "opacity-60" : ""}`} onClick={() => setSelected(i.path)}>
                    <td className="break-all font-mono text-code">{i.path}{i.is_test && <span className="chip ml-1 border-ok/40 text-ok">test</span>}</td>
                    <td className="font-mono tnum">{i.depth}</td>
                    <td className="font-mono text-micro">{i.confidence}</td>
                    <td className="text-body-sm text-muted">{i.reason}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section className="panel">
          <div className="panel-head"><span>Structure delta</span><span>+{r.graph.added_count} −{r.graph.removed_count} edges</span></div>
          <div className="space-y-3 p-3">
            {r.graph.layer_violations.length > 0 && (
              <div><div className="label-micro text-critical">New layer violations</div>{r.graph.layer_violations.map((v, i) => <div key={i} className="font-mono text-micro"><SeverityBadge severity={v.severity} /> {v.from_layer} → {v.to_layer}: {v.source} → {v.target}</div>)}</div>
            )}
            {r.graph.new_cycles.length > 0 && <div><div className="label-micro text-critical">New dependency cycles</div>{r.graph.new_cycles.map((c, i) => <div key={i} className="font-mono text-micro">{c.join(" ⟷ ")}</div>)}</div>}
            {r.graph.resolved_cycles.length > 0 && <div><div className="label-micro text-ok">Cycles resolved</div>{r.graph.resolved_cycles.map((c, i) => <div key={i} className="font-mono text-micro">{c.join(" ⟷ ")}</div>)}</div>}
            {r.graph.behaviour.length > 0 && <div><div className="label-micro">Behaviour tags</div>{r.graph.behaviour.map((b, i) => <div key={i} className={`font-mono text-micro ${b.change === "added" ? "text-warning" : "text-muted"}`}>{b.change === "added" ? "+" : "−"}{b.tag} · {b.path}</div>)}</div>}
            <div><div className="label-micro">Containers touched</div>{r.graph.containers.map((c) => <div key={c.container} className="font-mono text-micro">{c.container} · {c.changed} changed · {c.impacted} impacted</div>)}</div>
            {r.graph.added_edges.length > 0 && <details><summary className="cursor-pointer label-micro">Added edges ({r.graph.added_count})</summary>{r.graph.added_edges.map((e, i) => <div key={i} className="font-mono text-micro text-ok">+ {e.source} → {e.target} ({e.kind})</div>)}</details>}
            {r.graph.removed_edges.length > 0 && <details><summary className="cursor-pointer label-micro">Removed edges ({r.graph.removed_count})</summary>{r.graph.removed_edges.map((e, i) => <div key={i} className="font-mono text-micro text-critical">− {e.source} → {e.target} ({e.kind})</div>)}</details>}
          </div>
        </section>
      </div>
      <div className="grid gap-4 xl:grid-cols-2">
        <section className="panel">
          <div className="panel-head"><span>Dependencies</span><span>+{r.dependencies.added.length} −{r.dependencies.removed.length} ~{r.dependencies.changed.length}</span></div>
          <table className="w-full"><tbody>
            {r.dependencies.added.map((d) => <tr key={`a${d.name}`} className="table-row"><td className="font-mono text-code text-ok">+ {d.name}</td><td className="font-mono text-micro">{d.version}</td><td className="font-mono text-micro text-faint">{d.ecosystem}</td></tr>)}
            {r.dependencies.removed.map((d) => <tr key={`r${d.name}`} className="table-row"><td className="font-mono text-code text-critical">− {d.name}</td><td className="font-mono text-micro">{d.version}</td><td className="font-mono text-micro text-faint">{d.ecosystem}</td></tr>)}
            {r.dependencies.changed.map((d) => <tr key={`c${d.name}`} className="table-row"><td className="font-mono text-code">~ {d.name}{d.major && <span className="chip ml-1 border-critical/50 text-critical">major</span>}{d.downgrade && <span className="chip ml-1 border-warning/50 text-warning">downgrade</span>}</td><td className="font-mono text-micro">{d.before} → {d.after}</td><td className="font-mono text-micro text-faint">{d.ecosystem}</td></tr>)}
          </tbody></table>
          {!r.dependencies.added.length && !r.dependencies.removed.length && !r.dependencies.changed.length && <p className="p-3 text-body-sm text-muted">No dependency changes.</p>}
        </section>
        <section className="panel">
          <div className="panel-head"><span>Concerns</span><span>{r.concerns.new.length} new · {r.concerns.resolved.length} resolved</span></div>
          <div className="max-h-80 overflow-auto">
            {r.concerns.new.map((c, i) => <div key={`n${i}`} className="flex items-start gap-2 border-b border-line/60 px-3 py-1.5"><SeverityBadge severity={c.severity} /><div><div className="text-body-md">{c.title}</div><div className="font-mono text-micro text-faint">new · {c.category}{c.path && ` · ${c.path}`}</div></div></div>)}
            {r.concerns.resolved.map((c, i) => <div key={`r${i}`} className="flex items-start gap-2 border-b border-line/60 px-3 py-1.5 opacity-70"><span className="chip border-ok/50 text-ok">resolved</span><div><div className="text-body-md">{c.title}</div><div className="font-mono text-micro text-faint">{c.category}{c.path && ` · ${c.path}`}</div></div></div>)}
            {!r.concerns.new.length && !r.concerns.resolved.length && <p className="p-3 text-body-sm text-muted">No quality concerns appeared or disappeared.</p>}
          </div>
        </section>
      </div>
    </>
  );
}

// --- reviewers ----------------------------------------------------------------------------------------------------

function ReviewersTab({ run }: { run: ImpactRun }) {
  const r = run.result;
  return (
    <div className="grid gap-4 xl:grid-cols-2">
      <section className="panel">
        <div className="panel-head"><span>Suggested reviewers</span><span>CODEOWNERS + recent authors of the changed files</span></div>
        <table className="w-full">
          <thead className="table-head"><tr><th>Reviewer</th><th>Source</th><th>Files</th></tr></thead>
          <tbody>
            {r.reviewers.map((p) => (
              <tr key={p.name} className="table-row">
                <td className="font-mono text-code">{p.name}</td>
                <td className="text-body-sm text-muted">{p.source}</td>
                <td className="font-mono text-micro text-muted">{p.files.map(basename).join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!r.reviewers.length && <Empty icon="group" title="No reviewers found">Add a CODEOWNERS file, or analyse a repository with commit history.</Empty>}
      </section>
      <section className="panel">
        <div className="panel-head"><span>Hotspots · frequently changed files in this PR</span><span>last 180 days</span></div>
        {r.risk.hotspots.map((h) => <div key={h.path} className="flex justify-between border-b border-line/60 px-3 py-1.5 font-mono text-code"><span>{h.path}</span><span className="tnum text-warning">{h.commits} commits</span></div>)}
        {!r.risk.hotspots.length && <p className="p-3 text-body-sm text-muted">No changed file was modified five or more times recently.</p>}
      </section>
    </div>
  );
}
