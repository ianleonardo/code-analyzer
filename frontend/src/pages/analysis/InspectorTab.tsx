import { useEffect, useMemo, useState } from "react";
import { api } from "../../api";
import { CodeView } from "../../components/CodeView";
import { DependencyGraph, EDGE_KINDS, EDGE_VAR } from "../../components/DependencyGraph";
import { Breadcrumb, ConceptTag, LayerPill, RoleBadge, Select, SeverityBadge, Spinner, SplitPane } from "../../components/ui";
import type { Analysis, Blast, FileDetail, GraphEdge, GraphNode, Layer } from "../../types";

type GraphData = { nodes: GraphNode[]; edges: GraphEdge[]; total_nodes: number };

export function InspectorTab({ analysis, layers, file, line, onSelect, onBlast }: { analysis: Analysis; layers: Set<Layer>; file: string | null; line: number | null; onSelect: (path: string, line?: number) => void; onBlast: (blast: Blast | null) => void }) {
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [detail, setDetail] = useState<FileDetail | null>(null);
  const [kinds, setKinds] = useState<Set<string>>(new Set(["IMPORTS", "CALLS", "INJECTS", "EXTENDS", "IMPLEMENTS", "INCLUDES"]));
  const [limit, setLimit] = useState(120);
  const [focusMode, setFocusMode] = useState(false);
  const [pane, setPane] = useState<"code" | "neighbours" | "blast" | "concerns">("code");
  const [symbol, setSymbol] = useState<[number, number] | null>(null);
  const layerList = useMemo(() => (layers.size ? [...layers].join(",") : "__none__"), [layers]);

  useEffect(() => {
    const focus = focusMode && file ? `&focus=${encodeURIComponent(file)}` : "";
    api<GraphData>(`/api/analyses/${analysis.analysis_id}/graph?limit=${limit}&layers=${layerList}${focus}`).then(setGraph);
  }, [analysis.analysis_id, layerList, limit, focusMode, file]);

  useEffect(() => {
    if (!file) {
      setDetail(null);
      onBlast(null);
      return;
    }
    setDetail(null);
    api<FileDetail>(`/api/analyses/${analysis.analysis_id}/file?path=${encodeURIComponent(file)}`).then((data) => {
      setDetail(data);
      onBlast(data.blast_radius);
      const found = line ? data.symbols.find((s) => s.line_start === line) : null;
      setSymbol(found ? [found.line_start, found.line_end] : line ? [line, line] : null);
    });
  }, [analysis.analysis_id, file, line, onBlast]);

  const scope = useMemo(() => {
    if (!detail || !symbol) return "";
    return detail.symbols.find((s) => s.line_start === symbol[0])?.qualified_name ?? "";
  }, [detail, symbol]);

  return (
    <SplitPane
      direction="horizontal"
      primary="first"
      initialSize={580}
      minSize={260}
      maxSize={1100}
      firstTitle="Dependency Graph"
      secondTitle={detail ? `Inspector · ${detail.path}` : "File & Code Inspector"}
      first={
        <div className="flex h-full min-h-0 flex-col">
          <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface px-2 pr-9 py-1">
            {EDGE_KINDS.map((kind) => (
              <label key={kind} className="flex cursor-pointer items-center gap-1 font-mono text-micro" style={{ color: `rgb(var(--${EDGE_VAR[kind]}))` }}>
                <input type="checkbox" checked={kinds.has(kind)} onChange={(e) => setKinds((s) => { const next = new Set(s); if (e.target.checked) next.add(kind); else next.delete(kind); return next; })} />
                — {kind}
              </label>
            ))}
            <span className="ml-auto flex items-center gap-2">
              <label className="flex items-center gap-1 font-mono text-micro text-muted"><input type="checkbox" checked={focusMode} disabled={!file} onChange={(e) => setFocusMode(e.target.checked)} /> 2-hop focus</label>
              <Select className="field h-6 w-24" value={limit} onChange={(e) => setLimit(Number(e.target.value))}>
                {[60, 120, 200, 300].map((n) => <option key={n} value={n}>top {n}</option>)}
              </Select>
            </span>
          </div>
          <div className="min-h-0 flex-1">
            {graph ? <DependencyGraph nodes={graph.nodes} edges={graph.edges} selected={file} onSelect={(path) => onSelect(path)} kinds={kinds} /> : <div className="p-3"><Spinner label="Loading graph…" /></div>}
          </div>
        </div>
      }
      second={
        <div className="flex h-full min-h-0 flex-col">
          {!file && <div className="flex h-full items-center justify-center p-6 text-center text-muted">Select a node in the graph (or a file anywhere) to inspect its role, code, neighbourhood and blast radius.</div>}
          {file && !detail && <div className="p-3"><Spinner label="Loading file…" /></div>}
          {detail && (
            <>
              <div className="flex h-8 items-center gap-2 border-b border-line bg-surface px-3 pr-9">
                <Breadcrumb path={detail.path} scope={scope} />
                <span className="ml-auto flex shrink-0 items-center gap-1.5">
                  <LayerPill layer={detail.layer} />
                  <RoleBadge role={detail.role} confidence={detail.role_confidence} />
                </span>
              </div>
              <div className="border-b border-line bg-surface px-3 py-2">
                <div className="flex flex-wrap gap-x-4 gap-y-1 font-mono text-micro text-muted">
                  <span>McCabe: <b className="text-ink tnum">{detail.complexity}</b> (max fn {detail.max_function_complexity})</span>
                  <span>Fan-In: <b className="text-primary tnum">{detail.fan_in}</b></span>
                  <span>Fan-Out: <b className="text-primary tnum">{detail.fan_out}</b></span>
                  <span>PageRank: <b className="text-ink tnum">{detail.pagerank.toFixed(4)}</b></span>
                  <span>LOC: <b className="text-ink tnum">{detail.code_lines}</b>/{detail.loc}</span>
                  {detail.scc_id !== null && <span className="text-critical">SCC #{detail.scc_id}</span>}
                  {detail.is_entry && <span className="text-ok">ENTRY POINT</span>}
                  <span>{detail.language}</span>
                </div>
                {detail.summary && <p className="mt-1.5 text-body-md">{detail.summary} {detail.summary_source === "llm" && <span className="chip border-ok/40 text-ok">llm</span>}</p>}
                <div className="mt-1.5 flex flex-wrap gap-3">{detail.concepts.map((c) => <ConceptTag key={c} concept={c} />)}</div>
                {detail.evidence.length > 0 && <details className="mt-1"><summary className="cursor-pointer font-mono text-micro text-faint">classification evidence</summary><ul className="mt-1 font-mono text-micro text-muted">{detail.evidence.map((e, i) => <li key={i}>› {e}</li>)}</ul></details>}
              </div>
              <div className="flex border-b border-line bg-surface">
                {([
                  ["code", `Code · ${detail.symbols.length} symbols`],
                  ["neighbours", `1-hop neighbourhood · ${detail.incoming.length + detail.outgoing.length}`],
                  ["blast", `Blast radius · ${detail.blast_radius.impacted_count}`],
                  ["concerns", `Concerns · ${detail.concerns.length}`],
                ] as const).map(([key, label]) => (
                  <button key={key} type="button" className={`tab h-8 ${pane === key ? "tab-active" : ""}`} onClick={() => setPane(key)}>{label}</button>
                ))}
              </div>
              <div className="min-h-0 flex-1">
                {pane === "code" && (
                  <SplitPane
                    direction="horizontal"
                    primary="first"
                    initialSize={200}
                    minSize={120}
                    maxSize={460}
                    firstTitle={`Symbols · ${detail.path.split("/").pop()}`}
                    secondTitle={`Source Code · ${detail.path}`}
                    first={
                      <div className="h-full overflow-auto">
                        {detail.symbols.map((s) => (
                          <button key={`${s.qualified_name}:${s.line_start}`} type="button" className={`flex w-full items-center gap-1 px-2 py-0.5 text-left hover:bg-elevated ${symbol && symbol[0] === s.line_start ? "bg-elevated text-primary" : ""}`} style={{ paddingLeft: s.parent ? 18 : 8 }} onClick={() => setSymbol([s.line_start, s.line_end])}>
                            <span className="w-3 font-mono text-micro text-faint">{s.kind[0]}</span>
                            <span className="flex-1 truncate font-mono text-code">{s.name}</span>
                            <span className={`font-mono text-micro tnum ${s.complexity > 15 ? "text-critical" : s.complexity > 10 ? "text-warning" : "text-faint"}`}>{s.complexity}</span>
                          </button>
                        ))}
                        {!detail.symbols.length && <p className="p-2 text-body-sm text-faint">No symbols.</p>}
                      </div>
                    }
                    second={
                      <div className="h-full overflow-auto">
                        {detail.content !== null ? <CodeView content={detail.content} highlight={symbol} scrollTo={symbol?.[0] ?? null} /> : <p className="p-3 text-muted">Content not stored (binary or larger than the limit).</p>}
                      </div>
                    }
                  />
                )}
                {pane === "neighbours" && (
                  <SplitPane
                    direction="horizontal"
                    primary="first"
                    initialSize={280}
                    minSize={140}
                    maxSize={640}
                    firstTitle="Incoming Dependencies (Called / Imported By)"
                    secondTitle="Outgoing Dependencies (Depends On)"
                    first={
                      <div className="h-full overflow-auto">
                        <div className="panel-head pr-9"><span>Called by / imported by</span><span>{detail.incoming.length}</span></div>
                        {detail.incoming.map((edge, i) => (
                          <button key={i} type="button" className="flex w-full items-center gap-2 border-b border-line/50 px-2 py-1 text-left hover:bg-elevated" onClick={() => onSelect(edge.path)}>
                            <span className="font-mono text-micro" style={{ color: `rgb(var(--${EDGE_VAR[edge.kind] ?? "edge-imports"}))` }}>{edge.kind}</span>
                            <span className="flex-1 truncate font-mono text-code">{edge.path}</span>
                            <LayerPill layer={edge.layer} />
                          </button>
                        ))}
                      </div>
                    }
                    second={
                      <div className="h-full overflow-auto">
                        <div className="panel-head pr-9"><span>Depends on</span><span>{detail.outgoing.length}</span></div>
                        {detail.outgoing.map((edge, i) => (
                          <button key={i} type="button" className="flex w-full items-center gap-2 border-b border-line/50 px-2 py-1 text-left hover:bg-elevated" onClick={() => onSelect(edge.path)}>
                            <span className="font-mono text-micro" style={{ color: `rgb(var(--${EDGE_VAR[edge.kind] ?? "edge-imports"}))` }}>{edge.kind}</span>
                            <span className="flex-1 truncate font-mono text-code">{edge.path}</span>
                            <LayerPill layer={edge.layer} />
                          </button>
                        ))}
                      </div>
                    }
                  />
                )}
                {pane === "blast" && (
                  <div className="h-full overflow-auto">
                    <div className="flex flex-wrap items-center gap-4 border-b border-line px-3 py-2 font-mono text-code">
                      <span>Risk <b className={detail.blast_radius.risk < 0.25 ? "text-ok" : detail.blast_radius.risk < 0.5 ? "text-warning" : "text-critical"}>{detail.blast_radius.risk.toFixed(2)} {detail.blast_radius.risk_label}</b></span>
                      <span>{detail.blast_radius.impacted_count} impacted · {detail.blast_radius.direct} direct · {detail.blast_radius.test_count} tests</span>
                      <span className="flex gap-1">{Object.entries(detail.blast_radius.layers).map(([l, n]) => <LayerPill key={l} layer={l} count={n} />)}</span>
                    </div>
                    {detail.blast_radius.impacted.map((item) => (
                      <button key={item.path} type="button" className="flex w-full items-center gap-2 border-b border-line/50 px-3 py-1 text-left hover:bg-elevated" onClick={() => onSelect(item.path)}>
                        <span className="w-10 font-mono text-micro text-faint">d={item.depth}</span>
                        <span className={`flex-1 truncate font-mono text-code ${item.is_test ? "text-faint" : ""}`}>{item.path}</span>
                        <LayerPill layer={item.layer} />
                      </button>
                    ))}
                    {!detail.blast_radius.impacted.length && <p className="p-3 text-muted">Nothing depends on this file.</p>}
                  </div>
                )}
                {pane === "concerns" && (
                  <div className="h-full space-y-2 overflow-auto p-3">
                    {detail.concerns.map((c, i) => (
                      <div key={i} className="border border-line p-2">
                        <div className="flex items-center gap-2"><SeverityBadge severity={c.severity} /><span className="font-mono text-micro text-muted">{c.category}</span></div>
                        <p className="mt-1 text-body-md">{c.title}</p>
                        {c.evidence && <pre className="mt-1 whitespace-pre-wrap font-mono text-micro text-faint">{c.evidence.slice(0, 600)}</pre>}
                      </div>
                    ))}
                    {!detail.concerns.length && <p className="text-muted">No concerns reference this file.</p>}
                  </div>
                )}
              </div>
            </>
          )}
        </div>
      }
    />
  );
}
