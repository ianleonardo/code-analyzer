import { useEffect, useMemo, useState } from "react";
import { api, compact } from "../../api";
import { HealthGauge, LayerPill, Markdown, Meter, SectionTitle, SplitPane, healthColor } from "../../components/ui";
import { LAYERS, type Analysis, type FileRow, type Layer } from "../../types";

export const LANGUAGE_COLORS = ["#00E5FF", "#00E676", "#FFD54F", "#FF8A80", "#B39DDB", "#26C6DA", "#FF9100", "#EC407A", "#80CBC4", "#78909C"];

const CATEGORY_LABEL: Record<string, string> = {
  web: "Web / API", ui: "UI", orm: "ORM / migrations", database: "Databases", cache: "Cache", messaging: "Messaging", cloud: "Cloud",
  test: "Testing", build: "Build", validation: "Validation", security: "Security", http: "HTTP clients", ai: "AI / ML", data: "Data",
  cli: "CLI", logging: "Logging", resilience: "Resilience", runtime: "Runtime", config: "Config", workflow: "Workflow",
};

export function LanguageBar({ languages }: { languages: Record<string, number> }) {
  const entries = Object.entries(languages);
  const total = entries.reduce((sum, [, v]) => sum + v, 0) || 1;
  const top = entries.slice(0, 7);
  const rest = entries.slice(7).reduce((sum, [, v]) => sum + v, 0);
  const parts = rest ? [...top, ["Other", rest] as [string, number]] : top;
  return (
    <div>
      <div className="flex h-2 w-full overflow-hidden bg-elevated">
        {parts.map(([name, value], index) => (
          <div key={name} title={`${name} ${((100 * value) / total).toFixed(1)}%`} style={{ width: `${(100 * value) / total}%`, background: LANGUAGE_COLORS[index % LANGUAGE_COLORS.length] }} />
        ))}
      </div>
      <div className="mt-2 flex flex-wrap gap-x-3 gap-y-1">
        {parts.map(([name, value], index) => (
          <span key={name} className="inline-flex items-center gap-1 font-mono text-micro">
            <span className="h-1.5 w-1.5 rounded-full" style={{ background: LANGUAGE_COLORS[index % LANGUAGE_COLORS.length] }} />
            <span className="text-ink">{name}</span>
            <span className="text-faint tnum">{((100 * value) / total).toFixed(1)}%</span>
          </span>
        ))}
      </div>
    </div>
  );
}

function Stat({ label, value, hint, cls = "" }: { label: string; value: string | number; hint?: string; cls?: string }) {
  return (
    <div className="border border-line bg-surface px-3 py-2">
      <div className="label-micro">{label}</div>
      <div className={`font-mono text-head-lg tnum ${cls}`}>{value}</div>
      {hint && <div className="font-mono text-micro text-faint">{hint}</div>}
    </div>
  );
}

export function OverviewTab({ analysis, layers, onOpenFile }: { analysis: Analysis; layers: Set<Layer>; onOpenFile: (path: string) => void }) {
  const { stats, macro } = analysis;
  const [layerFiles, setLayerFiles] = useState<FileRow[] | null>(null);
  const layerList = useMemo(() => (layers.size ? [...layers].join(",") : "__none__"), [layers]);
  const isFiltered = layers.size < LAYERS.length;

  useEffect(() => {
    api<{ total: number; items: FileRow[] }>(
      `/api/analyses/${analysis.analysis_id}/files?layer=${layerList}&sort=pagerank&order=desc&include_tests=false&limit=200`,
    ).then((res) => setLayerFiles(res.items));
  }, [analysis.analysis_id, layerList]);

  const filteredSourceCount = useMemo(
    () => LAYERS.reduce((sum, l) => (layers.has(l) ? sum + (stats.layers?.[l] ?? 0) : sum), 0),
    [layers, stats.layers],
  );
  const topPagerank = useMemo(
    () =>
      layerFiles
        ? layerFiles.slice(0, 12).map((f) => ({ path: f.path, score: f.pagerank, layer: f.layer }))
        : (stats.top_pagerank ?? []).slice(0, 12).map((item) => ({ ...item, layer: undefined })),
    [layerFiles, stats.top_pagerank],
  );
  const entryPoints = useMemo(() => {
    if (!isFiltered || !layerFiles) return macro.entry_points.slice(0, 15);
    const allowedPaths = new Set(layerFiles.map((f) => f.path));
    return macro.entry_points.filter((p) => allowedPaths.has(p)).slice(0, 15);
  }, [isFiltered, layerFiles, macro.entry_points]);

  const groups: Record<string, string[]> = {};
  for (const fw of macro.frameworks ?? []) (groups[fw.category] ??= []).push(fw.name);
  const penalties = Object.entries(stats.penalties ?? {}).filter(([, v]) => v > 0).sort((a, b) => b[1] - a[1]);
  return (
    <SplitPane
      direction="horizontal"
      primary="second"
      initialSize={360}
      minSize={240}
      maxSize={640}
      firstTitle="Architecture & Centrality Overview"
      secondTitle="Stack, Entry Points & Health Penalties"
      first={
        <div className="h-full space-y-4 overflow-auto p-4 min-w-0">
        <section className="panel p-4">
          <div className="flex items-start gap-4">
            <HealthGauge score={analysis.health_score} size={72} />
            <div className="min-w-0 flex-1">
              <div className="flex flex-wrap items-center gap-2">
                <span className="chip border-primary/50 bg-primary/10 text-primary">{analysis.architecture_style}</span>
                {macro.monorepo && <span className="chip border-line-strong text-muted">monorepo</span>}
                {analysis.llm_used ? <span className="chip border-ok/50 text-ok">LLM synthesis · {stats.llm_provider}</span> : <span className="chip border-line-strong text-faint">deterministic summary</span>}
              </div>
              {macro.business_purpose && <p className="mt-2 text-body-lg font-medium">{macro.business_purpose}</p>}
              <Markdown text={analysis.summary} className="mt-2 text-muted" />
            </div>
          </div>
        </section>

        <div className="grid grid-cols-2 gap-2 md:grid-cols-4 xl:grid-cols-8">
          <Stat label="Files" value={compact(isFiltered ? filteredSourceCount : stats.files_source)} hint={isFiltered ? `${layers.size}/${LAYERS.length} layers active` : `${stats.files_total} total`} cls={isFiltered ? "text-primary" : ""} />
          <Stat label="Lines" value={compact(analysis.loc_total)} />
          <Stat label="Symbols" value={compact(stats.symbols)} />
          <Stat label="Edges" value={compact(stats.edges)} hint={`${stats.imports_resolved} imports resolved`} />
          <Stat label="McCabe avg" value={stats.avg_complexity} hint={`max ${stats.max_complexity}`} cls={stats.avg_complexity > 8 ? "text-warning" : ""} />
          <Stat label="SCC cycles" value={stats.cycles} cls={stats.cycles ? "text-critical" : "text-ok"} />
          <Stat label="Duplication" value={`${stats.duplication_pct}%`} cls={stats.duplication_pct > 5 ? "text-warning" : ""} />
          <Stat label="Tests" value={stats.test_files} hint={`ratio ${stats.test_ratio}`} cls={stats.test_files ? "" : "text-warning"} />
        </div>

        <section className="panel p-4">
          <SectionTitle>Polyglot share (lines)</SectionTitle>
          <LanguageBar languages={stats.languages ?? {}} />
        </section>

        <section className="panel">
          <div className="panel-head"><span>Containers ({analysis.containers.length})</span></div>
          <table className="w-full">
            <thead className="table-head"><tr><th>Container</th><th>Kind</th><th>Ecosystem</th><th>Frameworks</th><th className="text-right">Files</th><th className="text-right">LOC</th></tr></thead>
            <tbody>
              {analysis.containers.map((c) => (
                <tr key={c.container_id} className="table-row">
                  <td>
                    <div className="font-mono text-code">{c.name}</div>
                    <div className="font-mono text-micro text-faint">/{c.path} {c.manifest && `· ${c.manifest.split("/").pop()}`}</div>
                    {c.summary && <div className="mt-1 max-w-xl text-body-sm text-muted">{c.summary}</div>}
                  </td>
                  <td><span className="chip border-line-strong text-muted">{c.kind}</span></td>
                  <td className="font-mono text-code">{c.ecosystem}</td>
                  <td className="max-w-[280px]"><div className="flex flex-wrap gap-1">{c.frameworks.slice(0, 10).map((fw) => <span key={fw.name} className="chip border-line text-muted normal-case tracking-normal">{fw.name}</span>)}</div></td>
                  <td className="text-right font-mono tnum">{c.file_count}</td>
                  <td className="text-right font-mono tnum">{compact(c.loc)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        {(macro.key_capabilities?.length || macro.architecture_notes?.length || macro.risks?.length) ? (
          <div className="grid gap-4 md:grid-cols-3">
            {([["Key capabilities", macro.key_capabilities, "task_alt"], ["Architecture notes", macro.architecture_notes, "architecture"], ["Risks", macro.risks, "warning"]] as const).map(([title, items, icon]) => (
              <section key={title} className="panel p-3">
                <SectionTitle>{title}</SectionTitle>
                <ul className="space-y-1.5">
                  {(items ?? []).map((item, i) => (
                    <li key={i} className="flex gap-2 text-body-md"><span className="material-symbols-outlined icon-sm text-primary">{icon}</span><span>{item}</span></li>
                  ))}
                </ul>
              </section>
            ))}
          </div>
        ) : null}

        <section className="panel">
          <div className="panel-head">
            <span>Top centrality nodes (PageRank)</span>
            <span>{isFiltered ? `filtered (${layers.size}/${LAYERS.length} layers) · ` : ""}damping 0.85</span>
          </div>
          <ol className="divide-y divide-line/60">
            {topPagerank.map((item, index) => (
              <li key={item.path}>
                <button type="button" className="flex w-full items-center gap-3 px-3 py-1.5 text-left hover:bg-elevated" onClick={() => onOpenFile(item.path)}>
                  <span className="w-5 font-mono text-micro text-faint">{index + 1}.</span>
                  <span className="flex-1 truncate font-mono text-code">{item.path}</span>
                  {item.layer && <LayerPill layer={item.layer} />}
                  <span className="font-mono text-code text-primary tnum">{item.score.toFixed(3)}</span>
                </button>
              </li>
            ))}
            {!topPagerank.length && <li className="px-3 py-3 font-mono text-micro text-muted">No files match the selected canonical layers.</li>}
          </ol>
        </section>
        </div>
      }
      second={
        <div className="h-full space-y-4 overflow-auto p-4">
        <section className="panel p-3">
          <SectionTitle>Architecture style evidence</SectionTitle>
          <ul className="space-y-1">{macro.style_evidence.map((e, i) => <li key={i} className="font-mono text-micro text-muted">› {e}</li>)}</ul>
        </section>
        <section className="panel p-3">
          <SectionTitle>Technology stack (from manifests)</SectionTitle>
          {Object.keys(groups).length === 0 && <p className="text-muted text-body-md">No framework found in manifests.</p>}
          <div className="space-y-2">
            {Object.entries(groups).sort().map(([category, names]) => (
              <div key={category}>
                <div className="font-mono text-micro text-faint">{CATEGORY_LABEL[category] ?? category}</div>
                <div className="mt-0.5 flex flex-wrap gap-1">{names.map((n) => <span key={n} className="chip border-line text-ink normal-case tracking-normal">{n}</span>)}</div>
              </div>
            ))}
          </div>
        </section>
        <section className="panel p-3">
          <SectionTitle>Entry points</SectionTitle>
          <ul className="space-y-0.5">
            {entryPoints.map((path) => (
              <li key={path}><button type="button" className="truncate font-mono text-code text-left hover:text-primary" onClick={() => onOpenFile(path)}>{path}</button></li>
            ))}
            {!entryPoints.length && <li className="text-muted">{isFiltered ? "None in selected layers." : "None detected."}</li>}
          </ul>
        </section>
        <section className="panel p-3">
          <SectionTitle>External systems</SectionTitle>
          <ul className="space-y-1">
            {macro.external_systems.map((e) => (
              <li key={e.name} className="flex items-center justify-between gap-2" title={e.evidence}>
                <span className="font-mono text-code">{e.name}</span>
                <span className="chip border-line text-muted">{e.type}</span>
              </li>
            ))}
            {!macro.external_systems.length && <li className="text-muted">None detected.</li>}
          </ul>
        </section>
        <section className="panel p-3">
          <SectionTitle>Infrastructure & CI</SectionTitle>
          <ul className="space-y-1 font-mono text-micro">
            {(macro.infrastructure ?? []).slice(0, 12).map((item, i) => <li key={i}><span className="text-primary">{String(item.type)}</span> <span className="text-muted">{String(item.path)}</span></li>)}
            {macro.ci.map((path) => <li key={path}><span className="text-primary">CI</span> <span className="text-muted">{path}</span></li>)}
            {!macro.infrastructure?.length && !macro.ci.length && <li className="text-muted">None found.</li>}
          </ul>
        </section>
        {macro.drift.length > 0 && (
          <section className="panel p-3">
            <SectionTitle>Documentation drift</SectionTitle>
            <ul className="space-y-1.5">{macro.drift.map((d) => <li key={d.claim} className="text-body-sm text-warning">⚠ {d.detail}</li>)}</ul>
          </section>
        )}
        <section className="panel p-3">
          <SectionTitle>Health penalties</SectionTitle>
          <div className="space-y-1.5">
            {penalties.map(([key, value]) => (
              <div key={key}>
                <div className="flex justify-between font-mono text-micro"><span className="text-muted">{key.replace("_", " ")}</span><span className="text-critical tnum">−{value}</span></div>
                <Meter value={value} max={20} color="rgb(var(--critical))" />
              </div>
            ))}
            {!penalties.length && <p className={`font-mono text-code ${healthColor(100)}`}>No penalties.</p>}
          </div>
        </section>
        <section className="panel p-3">
          <SectionTitle>Hygiene gates (skipped)</SectionTitle>
          <ul className="space-y-0.5 font-mono text-micro">
            {Object.entries(stats.skipped ?? {}).map(([reason, count]) => <li key={reason} className="flex justify-between"><span className="text-muted">{reason}</span><span className="tnum">{count}</span></li>)}
          </ul>
        </section>
        </div>
      }
    />
  );
}
