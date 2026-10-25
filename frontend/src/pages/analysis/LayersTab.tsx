import { useEffect, useMemo, useState } from "react";
import { api, downloadFile, errorText, post } from "../../api";
import { useAuth } from "../../auth";
import { CodeView } from "../../components/CodeView";
import { Mermaid } from "../../components/Mermaid";
import { Empty, ErrorNote, LAYER_DOT, LayerPill, Modal, Spinner, SplitPane } from "../../components/ui";
import { can } from "../../permissions";
import type { Analysis } from "../../types";
import { ItemDetail, MethodBadge } from "./layers/ItemDetail";
import { KIND_ICON, type CatalogRow, type CatalogSummary, type Crud } from "./layers/types";

const LAYER_ORDER = ["api", "service", "data", "ui", "shared"] as const;
const LAYER_HINT: Record<string, string> = {
  api: "Endpoints with request / response structure, validation, security and status codes",
  service: "Services, business rules, transactions, scheduled jobs and event listeners",
  data: "Database technology, tables (DDL + ORM), repositories, queries, procedures, CRUD and ERD",
  ui: "Screens with fields, actions, navigation and the backend calls they make",
  shared: "DTOs, enums, exceptions, configuration keys, security, integrations and utilities",
};

function Badge({ row }: { row: CatalogRow }) {
  const b = row.badge ?? {};
  if (row.kind === "endpoint") return <span className="flex items-center gap-1">{b.secured && <span className="material-symbols-outlined icon-sm text-warning" title="Secured">lock</span>}{b.rules ? <span className="chip border-c-validation/40 text-ink">{b.rules} {b.rules === 1 ? "rule" : "rules"}</span> : null}</span>;
  if (row.kind === "table") return <span className="font-mono text-micro text-faint">{b.columns} cols{b.crud ? ` · ${b.crud}` : ""}</span>;
  if (row.kind === "screen") return <span className="font-mono text-micro text-faint">{b.fields} fields · {b.calls} calls</span>;
  if (row.kind === "query") return <span className="font-mono text-micro text-faint">{b.operation} {b.dialect}</span>;
  if (row.kind === "job") return <span className="truncate font-mono text-micro text-faint">{b.schedule}</span>;
  if (row.kind === "listener") return <span className="truncate font-mono text-micro text-faint">{b.transport}{b.destination ? ` · ${b.destination}` : ""}</span>;
  if (row.kind === "topic") return <span className="chip border-line-strong text-muted">{b.transport || "event"}</span>;
  if (row.kind === "config") return <span className="font-mono text-micro text-faint">{b.secret ? "secret · " : ""}{b.used ? `${b.used} uses` : b.category}</span>;
  if (["service", "view_model", "repository"].includes(row.kind)) return <span className="font-mono text-micro text-faint">{b.methods} methods</span>;
  if (["api_client", "integration"].includes(row.kind)) return b.matched ? <span className="material-symbols-outlined icon-sm text-ok" title="Matched to a backend endpoint">link</span> : null;
  return null;
}

function CrudView({ analysisId, onSelect }: { analysisId: string; onSelect: (id: string) => void }) {
  const [crud, setCrud] = useState<Crud | null>(null);
  useEffect(() => { api<Crud>(`/api/analyses/${analysisId}/catalog/crud`).then(setCrud); }, [analysisId]);
  if (!crud) return <div className="p-3"><Spinner /></div>;
  if (!crud.rows.length) return <Empty icon="table" title="No tables found" />;
  const actors = crud.actors.filter((a) => crud.rows.some((r) => r.cells[a.key]));
  return (
    <div className="overflow-auto p-3">
      <table className="border-collapse">
        <thead>
          <tr>
            <th className="sticky left-0 z-10 bg-surface p-1 text-left label-micro">Table \ actor</th>
            {actors.map((a) => <th key={a.key} className="max-h-60 min-w-[30px] border-l border-line px-1 align-bottom"><div className="mx-auto max-h-56 overflow-hidden whitespace-nowrap font-mono text-micro text-muted [writing-mode:vertical-rl] rotate-180" title={a.key}><span className="text-faint">{a.kind} </span>{a.name}</div></th>)}
          </tr>
        </thead>
        <tbody>
          {crud.rows.map((row) => (
            <tr key={row.item_id} className="border-t border-line">
              <th className="sticky left-0 bg-surface p-1 text-left"><button type="button" className="font-mono text-code hover:text-primary" onClick={() => onSelect(row.item_id)}>{row.table}</button></th>
              {actors.map((a) => {
                const ops = row.cells[a.key] ?? "";
                return <td key={a.key} className={`border-l border-line text-center font-mono text-micro ${ops.includes("C") || ops.includes("U") || ops.includes("D") ? "bg-warning/15 text-warning" : ops ? "bg-ok/10 text-ok" : ""}`}>{ops}</td>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
      <p className="mt-2 font-mono text-micro text-faint">C create · R read · U update · D delete — derived from repository / query usage, SQL statements and Spring Data method names.</p>
    </div>
  );
}

function ErdView({ analysisId }: { analysisId: string }) {
  const [erd, setErd] = useState<{ mermaid: string; tables: number; total: number } | null>(null);
  useEffect(() => { api<{ mermaid: string; tables: number; total: number }>(`/api/analyses/${analysisId}/catalog/erd`).then(setErd); }, [analysisId]);
  if (!erd) return <div className="p-3"><Spinner /></div>;
  if (!erd.tables) return <Empty icon="schema" title="No tables found" />;
  return (
    <div className="p-3">
      <p className="mb-2 font-mono text-micro text-faint">{erd.tables} of {erd.total} tables · keys and relationships from DDL, JPA / Hibernate / Room mappings and Liquibase</p>
      <Mermaid source={erd.mermaid} className="min-h-[480px]" />
    </div>
  );
}

type Source = { path: string; language: string; layer: string; role: string; loc: number; content: string | null };

/** Source code in a pop-up window, scrolled to and highlighting the item's lines. */
function CodePopup({ analysisId, path, line, end, onClose, onInspector }: { analysisId: string; path: string; line?: number; end?: number; onClose: () => void; onInspector: () => void }) {
  const [source, setSource] = useState<Source | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    setSource(null);
    api<Source>(`/api/analyses/${analysisId}/catalog/source?path=${encodeURIComponent(path)}`).then(setSource).catch((e) => setError(errorText(e)));
  }, [analysisId, path]);
  const highlight: [number, number] | null = line ? [line, Math.max(line, end ?? line)] : null;
  return (
    <Modal
      width="max-w-6xl"
      onClose={onClose}
      title={<span className="flex items-center gap-2 font-mono"><span className="material-symbols-outlined icon-sm">code</span>{path}{line ? <span className="text-primary">:{line}{end && end > line ? `–${end}` : ""}</span> : null}</span>}
      footer={
        <>
          <button type="button" className="btn" onClick={() => void navigator.clipboard?.writeText(`${path}${line ? `:${line}` : ""}`)}><span className="material-symbols-outlined icon-sm">content_copy</span>Copy path</button>
          <button type="button" className="btn" onClick={onInspector}><span className="material-symbols-outlined icon-sm">hub</span>Open in Graph & Code</button>
          <button type="button" className="btn btn-primary" onClick={onClose}>Close</button>
        </>
      }
    >
      {error && <ErrorNote message={error} />}
      {!source && !error && <Spinner label="Loading source…" />}
      {source && (
        <div className="flex h-[70vh] flex-col">
          <div className="mb-2 flex items-center gap-2 font-mono text-micro text-muted"><LayerPill layer={source.layer} /><span>{source.role}</span><span>· {source.language}</span><span>· {source.loc} lines</span></div>
          {source.content !== null ? <div className="min-h-0 flex-1 overflow-auto border border-line"><CodeView content={source.content} highlight={highlight} scrollTo={line ?? null} /></div> : <p className="p-3 text-muted">Content not stored (binary or larger than the limit).</p>}
        </div>
      )}
    </Modal>
  );
}

export function LayersTab({ analysis, layer, itemId, view, onNavigate, onOpenFile }: {
  analysis: Analysis; layer: string; itemId: string | null; view: string;
  onNavigate: (params: Record<string, string>) => void; onOpenFile: (path: string, line?: number) => void;
}) {
  const { user } = useAuth();
  const id = analysis.analysis_id;
  const [summary, setSummary] = useState<CatalogSummary | null>(null);
  const [rows, setRows] = useState<CatalogRow[] | null>(null);
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const [error, setError] = useState("");
  const [rebuilding, setRebuilding] = useState(false);
  const [code, setCode] = useState<{ path: string; line?: number; end?: number } | null>(null);
  const active = LAYER_ORDER.includes(layer as (typeof LAYER_ORDER)[number]) ? layer : "api";

  useEffect(() => {
    api<CatalogSummary>(`/api/analyses/${id}/catalog`).then(setSummary).catch((e) => setError(errorText(e)));
  }, [id]);
  useEffect(() => {
    if (!summary?.built) return;
    setRows(null);
    setKind("");
    api<{ items: CatalogRow[] }>(`/api/analyses/${id}/catalog/items?layer=${active}&limit=2000`).then((r) => setRows(r.items)).catch((e) => setError(errorText(e)));
  }, [id, active, summary]);

  const shown = useMemo(() => {
    const needle = q.trim().toLowerCase();
    return (rows ?? []).filter((r) => (!kind || r.kind === kind) && (!needle || `${r.name} ${r.path} ${r.signature} ${r.summary}`.toLowerCase().includes(needle)));
  }, [rows, kind, q]);
  const grouped = useMemo(() => {
    const out: Record<string, CatalogRow[]> = {};
    shown.forEach((r) => (out[r.kind] = [...(out[r.kind] ?? []), r]));
    return out;
  }, [shown]);

  const select = (item: string) => onNavigate({ layer: active, item });
  const nav = { onSelect: (item: string) => onNavigate({ layer: active, item }), onOpenFile: (path: string, line?: number, end?: number) => setCode({ path, line, end }) };

  async function rebuild() {
    setRebuilding(true);
    setError("");
    try {
      setSummary(await post<CatalogSummary>(`/api/analyses/${id}/catalog/rebuild`));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setRebuilding(false);
    }
  }

  if (error && !summary) return <div className="p-4"><ErrorNote message={error} /></div>;
  if (!summary) return <div className="p-4"><Spinner label="Loading layer catalogue…" /></div>;
  if (!summary.built) {
    return (
      <div className="p-6">
        <Empty icon="layers" title="No layer catalogue for this analysis yet">
          <p className="mt-1 text-body-md text-muted">This analysis was stored before the layer catalogue existed. It can be built now from the stored files, without cloning again.</p>
          {can(user, "repositories", "write") && (
            <button type="button" className="btn btn-primary mt-3" disabled={rebuilding} onClick={() => void rebuild()}>
              <span className="material-symbols-outlined icon-sm">construction</span>{rebuilding ? "Building…" : "Build layer catalogue"}
            </button>
          )}
          {error && <div className="mt-3"><ErrorNote message={error} /></div>}
        </Empty>
      </div>
    );
  }
  const layerInfo = summary.layers[active];
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-1 border-b border-line bg-surface px-2">
        {LAYER_ORDER.map((key) => (
          <button key={key} type="button" className={`tab ${active === key && view === "list" ? "tab-active" : ""}`} onClick={() => onNavigate({ layer: key })}>
            <span className={`h-2 w-2 ${LAYER_DOT[key]}`} />{summary.layers[key]?.label ?? key}<span className="font-mono text-micro text-faint tnum">{summary.layers[key]?.total ?? 0}</span>
          </button>
        ))}
        <span className="mx-1 h-5 w-px bg-line" />
        <button type="button" className={`tab ${view === "erd" ? "tab-active" : ""}`} onClick={() => onNavigate({ layer: "data", view: "erd" })}><span className="material-symbols-outlined icon-sm">schema</span>ERD</button>
        <button type="button" className={`tab ${view === "crud" ? "tab-active" : ""}`} onClick={() => onNavigate({ layer: "data", view: "crud" })}><span className="material-symbols-outlined icon-sm">grid_on</span>CRUD matrix</button>
        <div className="ml-auto flex items-center gap-1.5 py-1">
          <span className="hidden font-mono text-micro text-faint xl:inline">{summary.stats.items ?? "?"} items · {summary.links} links{summary.stats.llm_explained ? ` · ${summary.stats.llm_explained} AI-explained` : ""}</span>
          <button type="button" className="btn h-7" onClick={() => void downloadFile(`/api/analyses/${id}/catalog/export.md`, `layer-catalogue-${analysis.repo.slug || "repo"}.md`)}><span className="material-symbols-outlined icon-sm">description</span>Markdown</button>
          <button type="button" className="btn h-7" onClick={() => void downloadFile(`/api/analyses/${id}/catalog/export.xlsx`, `layer-catalogue-${analysis.repo.slug || "repo"}.xlsx`)}><span className="material-symbols-outlined icon-sm">table_view</span>Excel</button>
          {can(user, "repositories", "write") && <button type="button" className="icon-btn" title="Rebuild the catalogue from the stored files" disabled={rebuilding} onClick={() => void rebuild()}><span className={`material-symbols-outlined ${rebuilding ? "animate-spin" : ""}`}>refresh</span></button>}
        </div>
      </div>
      {error && <div className="p-2"><ErrorNote message={error} /></div>}
      {code && <CodePopup analysisId={id} path={code.path} line={code.line} end={code.end} onClose={() => setCode(null)} onInspector={() => { const target = code; setCode(null); onOpenFile(target.path, target.line); }} />}
      {view === "erd" && <div className="min-h-0 flex-1 overflow-auto"><ErdView analysisId={id} /></div>}
      {view === "crud" && <div className="min-h-0 flex-1 overflow-auto"><CrudView analysisId={id} onSelect={(item) => onNavigate({ layer: "data", item })} /></div>}
      {view === "list" && (
        <SplitPane
          direction="horizontal"
          primary="first"
          initialSize={380}
          minSize={240}
          maxSize={680}
          firstTitle={`${layerInfo?.label ?? active} Layer Catalogue`}
          secondTitle="Catalogue Item Detail & End-to-End Flow"
          className="min-h-0 flex-1"
          first={
            <div className="flex h-full min-h-0 flex-col">
              <div className="space-y-2 border-b border-line bg-surface p-2 pr-9">
                <p className="text-body-sm text-muted">{LAYER_HINT[active]}</p>
                <div className="flex flex-wrap gap-1">
                  <button type="button" className={`chip ${!kind ? "border-primary text-primary" : "border-line-strong text-muted"}`} onClick={() => setKind("")}>All {layerInfo?.total ?? 0}</button>
                  {(layerInfo?.kinds ?? []).map((k) => (
                    <button key={k.kind} type="button" className={`chip ${kind === k.kind ? "border-primary text-primary" : "border-line-strong text-muted"}`} onClick={() => setKind(kind === k.kind ? "" : k.kind)}>
                      <span className="material-symbols-outlined icon-sm">{KIND_ICON[k.kind] ?? "circle"}</span>{k.label}<span className="tnum">{k.count}</span>
                    </button>
                  ))}
                </div>
                <input className="field h-7 w-full" placeholder={`Filter ${layerInfo?.label ?? ""} items — name, path, signature…`} value={q} onChange={(e) => setQ(e.target.value)} />
              </div>
              <div className="min-h-0 flex-1 overflow-auto">
                {!rows && <div className="p-3"><Spinner /></div>}
                {rows && !shown.length && <p className="p-6 text-center text-muted">Nothing in this layer{q || kind ? " matches" : ""}.</p>}
                {Object.entries(grouped).map(([groupKind, items]) => (
                  <div key={groupKind}>
                    <div className="sticky top-0 z-10 flex items-center gap-1 border-b border-line bg-elevated px-2 py-1 label-micro">
                      <span className="material-symbols-outlined icon-sm">{KIND_ICON[groupKind] ?? "circle"}</span>
                      {layerInfo?.kinds.find((k) => k.kind === groupKind)?.label ?? groupKind}<span className="ml-auto tnum">{items.length}</span>
                    </div>
                    <ul>
                      {items.map((row) => (
                        <li key={row.item_id}>
                          <button type="button" className={`flex w-full flex-col gap-0.5 border-b border-line px-2 py-1.5 text-left hover:bg-elevated ${itemId === row.item_id ? "bg-primary/10 outline outline-1 -outline-offset-1 outline-primary" : ""}`} onClick={() => select(row.item_id)}>
                            <div className="flex w-full items-center gap-2">
                              {(row.kind === "endpoint" || row.kind === "api_client" || row.kind === "integration") && row.badge?.method ? <MethodBadge method={row.badge.method} /> : null}
                              <span className="min-w-0 flex-1 truncate font-mono text-code">{row.kind === "endpoint" || row.kind === "api_client" ? row.name.replace(/^\w+\s/, "") : row.name}</span>
                              <Badge row={row} />
                            </div>
                            {row.summary ? <span className="line-clamp-1 text-body-sm text-muted">{row.summary}</span> : <span className="truncate font-mono text-micro text-faint">{row.signature || row.path}</span>}
                          </button>
                        </li>
                      ))}
                    </ul>
                  </div>
                ))}
              </div>
            </div>
          }
          second={
            <div className="h-full min-h-0 overflow-auto">
              {itemId ? <ItemDetail analysisId={id} itemId={itemId} nav={nav} /> : <Empty icon="ads_click" title="Select an item">{LAYER_HINT[active]}. Click any item to see its details, rules and links to the other layers; “Trace flow” draws it end to end.</Empty>}
            </div>
          }
        />
      )}
    </div>
  );
}
