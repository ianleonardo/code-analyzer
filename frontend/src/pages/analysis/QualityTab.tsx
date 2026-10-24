import { useEffect, useMemo, useState } from "react";
import { api, downloadFile } from "../../api";
import { LayerPill, Meter, SEVERITY_ICON, Select, SeverityBadge, Spinner, SplitPane } from "../../components/ui";
import { LAYERS, type Analysis, type Concern, type Layer } from "../../types";
import { LanguageBar } from "./OverviewTab";

export function QualityTab({ analysis, layers, severity, category, onOpenFile, onFilter }: { analysis: Analysis; layers: Set<Layer>; severity: string; category: string; onOpenFile: (path: string) => void; onFilter: (f: Record<string, string>) => void }) {
  const [concerns, setConcerns] = useState<Concern[] | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const stats = analysis.stats;
  const layerList = useMemo(() => (layers.size ? [...layers].join(",") : "__none__"), [layers]);

  useEffect(() => {
    api<Concern[]>(`/api/analyses/${analysis.analysis_id}/concerns?layers=${layerList}`).then(setConcerns);
  }, [analysis.analysis_id, layerList]);

  const categories = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const c of concerns ?? []) counts[c.category] = (counts[c.category] ?? 0) + 1;
    return Object.entries(counts).sort((a, b) => b[1] - a[1]);
  }, [concerns]);
  const shown = (concerns ?? []).filter((c) => (!severity || c.severity === severity) && (!category || c.category === category));

  return (
    <SplitPane
      direction="horizontal"
      primary="second"
      initialSize={380}
      minSize={240}
      maxSize={680}
      firstTitle="Architectural & MVC Smell Registry"
      secondTitle="Tarjan SCC Cycles & Statistical Distributions"
      first={
        <div className="flex h-full min-h-0 flex-col">
          <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface px-3 pr-9 py-1.5">
            <span className="material-symbols-outlined text-critical">rule</span>
            <span className="text-head-sm">Architectural & MVC smell registry</span>
            <span className="chip border-line-strong text-muted">{shown.length} shown</span>
            {layers.size < LAYERS.length && (
              <span className="chip border-primary/50 bg-primary/10 text-primary">{layers.size}/{LAYERS.length} layers</span>
            )}
            <Select className="field h-7 w-36" value={severity} onChange={(e) => onFilter({ ...(category ? { category } : {}), ...(e.target.value ? { severity: e.target.value } : {}) })}>
              <option value="">All severities</option>
              <option value="critical">critical</option>
              <option value="warning">warning</option>
              <option value="info">info</option>
            </Select>
            <Select className="field h-7 w-56" value={category} onChange={(e) => onFilter({ ...(severity ? { severity } : {}), ...(e.target.value ? { category: e.target.value } : {}) })}>
              <option value="">All categories</option>
              {categories.map(([name, count]) => <option key={name} value={name}>{name} ({count})</option>)}
            </Select>
            <button type="button" className="btn ml-auto" onClick={() => void downloadFile(`/api/analyses/${analysis.analysis_id}/concerns.md`, `CONCERNS-${analysis.repo.slug || "repo"}.md`)}>
              <span className="material-symbols-outlined icon-sm">download</span>Export CONCERNS.md
            </button>
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            {!concerns && <div className="p-3"><Spinner /></div>}
            <table className="w-full">
              <thead className="table-head"><tr><th>File / symbol scope</th><th>Detected concern</th><th>Severity</th><th>Metric evidence</th></tr></thead>
              <tbody>
                {shown.map((c, index) => {
                  const key = c.concern_id ?? String(index);
                  return (
                    <tr key={key} className="table-row cursor-pointer" onClick={() => setExpanded(expanded === key ? null : key)}>
                      <td className="w-[30%] max-w-[320px]">
                        {c.path ? (
                          <div className="flex items-center gap-1.5">
                            <button type="button" title={c.path} className="truncate text-left font-mono text-code text-primary hover:underline" onClick={(e) => { e.stopPropagation(); onOpenFile(c.path); }}>{c.path}</button>
                            {c.layer && <LayerPill layer={c.layer} />}
                          </div>
                        ) : (
                          <span className="text-faint">repository</span>
                        )}
                      </td>
                      <td className="max-w-[360px]">
                        <div className={`flex items-center gap-1 font-mono text-code ${c.severity === "critical" ? "text-critical" : c.severity === "warning" ? "text-warning" : "text-info"}`}>
                          <span className="material-symbols-outlined icon-sm">{SEVERITY_ICON[c.severity]}</span>{c.category}
                        </div>
                        <div className="text-body-md">{c.title}</div>
                        {expanded === key && c.detail && <p className="mt-1 text-body-sm text-muted">{c.detail}</p>}
                      </td>
                      <td><SeverityBadge severity={c.severity} /></td>
                      <td className="max-w-[340px]"><pre className={`whitespace-pre-wrap font-mono text-micro text-muted ${expanded === key ? "" : "line-clamp-3"}`}>{c.evidence}</pre></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
            {concerns && !shown.length && <p className="p-6 text-center text-muted">No concerns match.</p>}
          </div>
        </div>
      }
      second={
        <div className="h-full min-h-0 space-y-3 overflow-auto p-3">
          <section className="panel">
            <div className="panel-head"><span className="flex items-center gap-1"><span className="material-symbols-outlined icon-sm text-critical">cyclone</span>Tarjan SCC dependency cycles</span><span className={stats.cycles ? "text-critical" : "text-ok"}>{stats.cycles} detected</span></div>
            <div className="space-y-2 p-3">
              {(stats.sccs ?? []).slice(0, 8).map((component, index) => (
                <div key={index} className="border border-critical/40 bg-critical/5 p-2">
                  <div className="font-mono text-micro text-critical">SCC #{index + 1} · {component.length} files (condensed super-node)</div>
                  <ul className="mt-1 space-y-0.5">{component.slice(0, 8).map((path) => <li key={path}><button type="button" className="truncate font-mono text-code hover:text-primary" onClick={() => onOpenFile(path)}>{path}</button></li>)}</ul>
                </div>
              ))}
              {!stats.cycles && <p className="text-body-md text-ok">The dependency graph is acyclic.</p>}
            </div>
          </section>
          <section className="panel p-3">
            <div className="label-micro mb-2">Codebase statistical distributions</div>
            <div className="space-y-3">
              <div><div className="mb-1 font-mono text-micro text-muted">Polyglot share</div><LanguageBar languages={stats.languages ?? {}} /></div>
              {[
                ["McCabe average", stats.avg_complexity, 20, `max ${stats.max_complexity}`],
                ["Duplicated lines %", stats.duplication_pct, 30, ""],
                ["Test ratio", Math.round(stats.test_ratio * 100), 100, `${stats.test_files} test files`],
                ["Layer-violating edges", stats.layer_violations, 30, ""],
                ["Possibly unused files", stats.unused_files, 50, ""],
                ["TODO / FIXME markers", stats.todo_markers, 100, ""],
              ].map(([label, value, max, hint]) => (
                <div key={String(label)}>
                  <div className="flex justify-between font-mono text-micro"><span className="text-muted">{label}</span><span className="tnum">{value} <span className="text-faint">{hint}</span></span></div>
                  <Meter value={Number(value)} max={Number(max)} />
                </div>
              ))}
            </div>
          </section>
          <section className="panel p-3">
            <div className="label-micro mb-2">Concerns by category</div>
            {categories.map(([name, count]) => (
              <button key={name} type="button" className="flex w-full justify-between py-0.5 font-mono text-code hover:text-primary" onClick={() => onFilter({ category: name })}>
                <span>{name}</span><span className="tnum">{count}</span>
              </button>
            ))}
          </section>
        </div>
      }
    />
  );
}
