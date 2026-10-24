/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useState, type ReactNode } from "react";
import { api, errorText } from "../../../api";
import { Mermaid } from "../../../components/Mermaid";
import { ErrorNote, LayerPill, Spinner } from "../../../components/ui";
import { KIND_ICON, METHOD_CLASS, type CatalogItem, type Details, type LinkRef, type Trace } from "./types";

type Nav = { onSelect: (itemId: string) => void; onOpenFile: (path: string, line?: number, end?: number) => void };

export function MethodBadge({ method }: { method: string }) {
  return <span className={`chip w-14 justify-center ${METHOD_CLASS[method] ?? METHOD_CLASS.ANY}`}>{method || "—"}</span>;
}

function Section({ title, right, children, empty }: { title: ReactNode; right?: ReactNode; children: ReactNode; empty?: boolean }) {
  if (empty) return null;
  return (
    <section className="panel">
      <div className="panel-head"><span>{title}</span>{right}</div>
      <div className="p-2.5">{children}</div>
    </section>
  );
}

function KV({ rows }: { rows: [string, ReactNode][] }) {
  const shown = rows.filter(([, v]) => v !== undefined && v !== null && v !== "" && !(Array.isArray(v) && !v.length));
  if (!shown.length) return null;
  return (
    <dl className="grid grid-cols-[150px_1fr] gap-x-3 gap-y-1">
      {shown.map(([k, v]) => (
        <div key={k} className="contents">
          <dt className="label-micro pt-0.5">{k}</dt>
          <dd className="min-w-0 break-words font-mono text-code">{v}</dd>
        </div>
      ))}
    </dl>
  );
}

function Chips({ values, className = "border-line-strong text-muted" }: { values: (string | number)[]; className?: string }) {
  if (!values?.length) return <span className="text-faint">—</span>;
  return <div className="flex flex-wrap gap-1">{values.map((v, i) => <span key={`${v}-${i}`} className={`chip normal-case tracking-normal ${className}`}>{v}</span>)}</div>;
}

function Table({ head, rows }: { head: string[]; rows: ReactNode[][] }) {
  if (!rows.length) return <p className="font-mono text-micro text-faint">None.</p>;
  return (
    <div className="overflow-x-auto">
      <table className="w-full">
        <thead className="table-head"><tr>{head.map((h) => <th key={h}>{h}</th>)}</tr></thead>
        <tbody>{rows.map((cells, i) => <tr key={i} className="table-row">{cells.map((c, j) => <td key={j} className="align-top font-mono text-code">{c}</td>)}</tr>)}</tbody>
      </table>
    </div>
  );
}

function Validations({ values }: { values?: string[] }) {
  if (!values?.length) return <span className="text-faint">—</span>;
  return <div className="flex flex-wrap gap-1">{values.map((v) => <span key={v} className="chip border-c-validation/40 text-ink">{v}</span>)}</div>;
}

function Code({ text, lang }: { text: string; lang?: string }) {
  if (!text) return null;
  return <pre className="max-h-80 overflow-auto whitespace-pre-wrap border border-line bg-inset p-2 font-mono text-micro" data-lang={lang}>{text}</pre>;
}

function FieldRows(fields: any[], depth = 0): ReactNode[][] {
  const rows: ReactNode[][] = [];
  for (const f of fields ?? []) {
    rows.push([
      <span key="n" style={{ paddingLeft: depth * 14 }} className={depth ? "text-muted" : ""}>{depth ? "└ " : ""}{f.name}</span>,
      <span key="t" className="text-primary">{f.type}{f.enum ? ` {${f.enum.slice(0, 6).join("|")}${f.enum.length > 6 ? "…" : ""}}` : ""}</span>,
      f.required ? <span key="r" className="text-warning">required</span> : <span key="r" className="text-faint">optional</span>,
      <Validations key="v" values={f.validations} />,
    ]);
    if (f.fields?.length && depth < 2) rows.push(...FieldRows(f.fields, depth + 1));
  }
  return rows;
}

function Rules({ rules, llm, onLine }: { rules?: any[]; llm?: any[]; onLine: (line: number) => void }) {
  if (!rules?.length && !llm?.length) return null;
  return (
    <Section title="Validation & processing rules" right={<span>{(rules?.length ?? 0) + (llm?.length ?? 0)} rules</span>}>
      <ol className="space-y-1.5">
        {(rules ?? []).map((r, i) => (
          <li key={`d${i}`} className="flex gap-2 border-l-2 border-c-validation pl-2">
            <span className="chip shrink-0 border-line-strong text-muted">{r.kind}</span>
            <div className="min-w-0 text-body-sm">
              <span className="text-muted">if </span><code className="font-mono text-code text-ink">{r.condition}</code>
              <span className="text-muted"> → </span><span>{r.outcome}</span>
              {r.line ? <button type="button" className="ml-2 font-mono text-micro text-primary hover:underline" onClick={() => onLine(r.line)}>L{r.line}</button> : null}
            </div>
          </li>
        ))}
        {(llm ?? []).map((r, i) => (
          <li key={`l${i}`} className="flex gap-2 border-l-2 border-primary pl-2">
            <span className="chip shrink-0 border-primary/40 text-primary" title="Explained by the LLM from the code; cited line in brackets">AI</span>
            <div className="text-body-sm">{r.rule}{r.line ? <button type="button" className="ml-2 font-mono text-micro text-primary hover:underline" onClick={() => onLine(r.line)}>L{r.line}</button> : null}</div>
          </li>
        ))}
      </ol>
    </Section>
  );
}

function LinkGroups({ title, links, nav }: { title: string; links: LinkRef[]; nav: Nav }) {
  if (!links.length) return null;
  const groups: Record<string, LinkRef[]> = {};
  links.forEach((l) => (groups[l.relation] = [...(groups[l.relation] ?? []), l]));
  return (
    <Section title={title} right={<span>{links.length}</span>}>
      <div className="space-y-2">
        {Object.entries(groups).map(([relation, items]) => (
          <div key={relation}>
            <div className="label-micro mb-1">{relation}</div>
            <ul className="space-y-0.5">
              {items.slice(0, 60).map((l, i) => (
                <li key={`${l.item_id}-${i}`}>
                  <button type="button" className="flex w-full items-center gap-2 text-left hover:text-primary" onClick={() => nav.onSelect(l.item_id)}>
                    <LayerPill layer={l.layer} />
                    <span className="material-symbols-outlined icon-sm text-muted">{KIND_ICON[l.kind] ?? "circle"}</span>
                    <span className="truncate font-mono text-code">{l.name}</span>
                    {l.detail && <span className="truncate font-mono text-micro text-faint">{l.detail}</span>}
                  </button>
                </li>
              ))}
            </ul>
          </div>
        ))}
      </div>
    </Section>
  );
}

function TracePanel({ analysisId, itemId }: { analysisId: string; itemId: string }) {
  const [trace, setTrace] = useState<Trace | null>(null);
  const [mode, setMode] = useState<"flow" | "sequence">("flow");
  const [error, setError] = useState("");
  useEffect(() => {
    setTrace(null);
    api<Trace>(`/api/analyses/${analysisId}/catalog/items/${itemId}/trace`).then(setTrace).catch((e) => setError(errorText(e)));
  }, [analysisId, itemId]);
  if (error) return <ErrorNote message={error} />;
  if (!trace) return <Spinner label="Tracing…" />;
  if (!trace.edges.length) return <p className="font-mono text-micro text-faint">No cross-layer links from this item.</p>;
  return (
    <div>
      <div className="mb-2 flex items-center gap-2">
        <div className="grid grid-cols-2 border border-line">
          {(["flow", "sequence"] as const).map((m) => (
            <button key={m} type="button" disabled={m === "sequence" && !trace.sequence} className={`h-7 px-3 font-mono text-micro ${mode === m ? "bg-primary/10 text-primary" : "text-muted hover:text-ink disabled:opacity-40"}`} onClick={() => setMode(m)}>
              {m === "flow" ? "Flow (upstream → downstream)" : "Sequence"}
            </button>
          ))}
        </div>
        <span className="font-mono text-micro text-faint">{trace.nodes.length} items · {trace.edges.length} links</span>
      </div>
      <Mermaid source={mode === "flow" ? trace.flowchart : trace.sequence} className="min-h-[260px]" />
    </div>
  );
}

function ErdPanel({ analysisId, keys }: { analysisId: string; keys: string }) {
  const [source, setSource] = useState("");
  useEffect(() => {
    api<{ mermaid: string }>(`/api/analyses/${analysisId}/catalog/erd?keys=${encodeURIComponent(keys)}`).then((r) => setSource(r.mermaid));
  }, [analysisId, keys]);
  return source ? <Mermaid source={source} className="min-h-[220px]" /> : <Spinner />;
}

function EndpointBody({ d, nav, line }: { d: Details; nav: Nav; line: (n: number) => void }) {
  const params = (d.params ?? []).filter((p: any) => p.in !== "context" && p.in !== "body");
  const context = (d.params ?? []).filter((p: any) => p.in === "context");
  const security = d.security ?? {};
  return (
    <>
      <Section title="Interface">
        <KV rows={[
          ["Method · path", <span className="flex items-center gap-2"><MethodBadge method={d.method} /><span>{d.path}</span></span>],
          ["Full URL", d.url && d.url !== d.path ? d.url : ""],
          ["Style", d.style],
          ["Handler", d.handler ? <button type="button" className="link" onClick={() => nav.onOpenFile(d.handler.path, d.handler.line)}>{d.handler.class ?? d.handler.function}.{d.handler.method ?? ""}</button> : ""],
          ["Consumes", (d.consumes ?? []).join(", ") || (d.request?.content_type ?? "")],
          ["Produces", (d.produces ?? []).join(", ") || (d.response?.content_type ?? "")],
          ["Status codes", <Chips values={d.status_codes ?? []} />],
          ["Middlewares", (d.middlewares ?? []).join(", ")],
          ["Operation (spec)", d.openapi ? `${d.openapi.operation_id || ""} ${d.openapi.summary ? "— " + d.openapi.summary : ""}` : ""],
        ]} />
      </Section>
      <Section title="Parameters" empty={!params.length}>
        <Table head={["In", "Name", "Type", "Required", "Validation", "Default"]} rows={params.map((p: any) => [p.in, p.name, <span className="text-primary">{p.type}</span>, p.required ? <span className="text-warning">yes</span> : "", <Validations values={p.validations} />, p.default ?? ""])} />
        {context.length > 0 && <p className="mt-1 font-mono text-micro text-faint">Framework-injected: {context.map((p: any) => `${p.type} ${p.name}`).join(", ")}</p>}
      </Section>
      <Section title={<>Request body {d.request?.name ? <code className="ml-1 text-primary">{d.request.name}</code> : null}</>} empty={!d.request} right={d.request ? <span>{d.request.content_type}{d.request.validated ? " · @Valid" : ""}{d.request.source ? ` · ${d.request.source}` : ""}</span> : null}>
        <Table head={["Field", "Type", "", "Validation"]} rows={FieldRows(d.request?.fields ?? [])} />
        {d.request?.path && <button type="button" className="link mt-1 font-mono text-micro" onClick={() => nav.onOpenFile(d.request.path, d.request.line)}>{d.request.path}:{d.request.line}</button>}
      </Section>
      <Section title={<>Response {d.response?.type ? <code className="ml-1 text-primary">{d.response.collection ? `${d.response.name}[]` : d.response.type}</code> : null}</>} empty={!d.response || (!d.response.fields?.length && !d.response.views?.length && !d.response.type)}>
        {d.response?.kind === "view" ? (
          <KV rows={[["Renders view", (d.response.views ?? []).join(", ")], ["Redirects to", (d.response.redirects ?? []).join(", ")], ["Model attributes", (d.model_attributes ?? []).join(", ")]]} />
        ) : (
          <Table head={["Field", "Type", "", "Validation"]} rows={FieldRows(d.response?.fields ?? [])} />
        )}
      </Section>
      <Section title="Security" empty={!Object.keys(security).length}>
        <KV rows={[
          ["Roles", security.roles?.length ? <Chips values={security.roles} className="border-warning/40 text-warning" /> : ""],
          ["Expressions", (security.expressions ?? []).join(" · ")],
          ["HTTP rules", (security.http_rules ?? (security.http_rule ? [security.http_rule] : [])).length ? (
            <ul>{(security.http_rules ?? [security.http_rule]).map((r: any, i: number) => <li key={i}><span className={r.access === "permitAll" ? "text-ok" : "text-warning"}>{r.access}{r.roles?.length ? `(${r.roles.join(", ")})` : ""}</span> <span className="text-faint">— {r.pattern} in {r.config}</span></li>)}</ul>
          ) : ""],
          ["Public", security.public ? "yes (@PermitAll)" : ""],
        ]} />
      </Section>
      <Section title="Errors" empty={!d.errors?.length}>
        <Table head={["Exception", "Status", "Message", "Handled by", "Line"]} rows={(d.errors ?? []).map((e: any) => [e.exception, e.status || "—", e.message, e.handled_by ?? "", <button type="button" className="link" onClick={() => line(e.line)}>L{e.line}</button>])} />
      </Section>
      <Rules rules={d.rules} llm={d.llm_rules} onLine={line} />
      <Section title="Outbound calls" empty={!d.http_calls?.length && !d.events?.length}>
        <Table head={["Kind", "Target", "Line"]} rows={[...(d.http_calls ?? []).map((h: any) => [`${h.client} ${h.method}`, h.url, `L${h.line}`]), ...(d.events ?? []).map((e: any) => [e.kind, e.name, `L${e.line}`])]} />
      </Section>
    </>
  );
}

function MethodsTable({ methods, line }: { methods: any[]; line: (n: number) => void }) {
  const [open, setOpen] = useState<string | null>(null);
  if (!methods?.length) return null;
  return (
    <Section title="Methods / operations" right={<span>{methods.length}</span>}>
      <ul className="divide-y divide-line">
        {methods.map((m, i) => {
          const key = `${m.name}-${i}`;
          const extra = (m.rules?.length ?? 0) + (m.throws?.length ?? 0) + (m.sql?.length ?? 0);
          return (
            <li key={key} className="py-1.5">
              <button type="button" className="flex w-full items-start gap-2 text-left" onClick={() => setOpen(open === key ? null : key)}>
                <span className="material-symbols-outlined icon-sm text-muted">{open === key ? "expand_more" : "chevron_right"}</span>
                <code className="min-w-0 flex-1 break-all font-mono text-code">{m.signature ?? m.name}</code>
                {m.transactional && <span className="chip border-c-tx/40 text-ink" title={JSON.stringify(m.transactional)}>tx{m.transactional.read_only ? " ro" : ""}</span>}
                {m.operation && <span className="chip border-line-strong text-muted">{m.operation}</span>}
                {m.async && <span className="chip border-line-strong text-muted">async</span>}
                {extra > 0 && <span className="chip border-c-validation/40 text-ink">{extra} rules</span>}
              </button>
              {open === key && (
                <div className="ml-6 mt-1 space-y-1.5 text-body-sm">
                  {m.line ? <button type="button" className="link font-mono text-micro" onClick={() => line(m.line)}>line {m.line}{m.end_line ? `–${m.end_line}` : ""}</button> : null}
                  {m.annotations?.length > 0 && <Chips values={m.annotations} />}
                  {m.calls?.length > 0 && <div><span className="label-micro">calls </span><span className="font-mono text-code">{m.calls.join(", ")}</span></div>}
                  {m.rules?.map((r: any, j: number) => <div key={j} className="border-l-2 border-c-validation pl-2">if <code>{r.condition}</code> → {r.outcome} <span className="font-mono text-micro text-faint">L{r.line}</span></div>)}
                  {m.throws?.map((t: any, j: number) => <div key={`t${j}`} className="font-mono text-micro text-critical">throws {t.exception}{t.message ? `: ${t.message}` : ""}</div>)}
                  {m.sql?.map((s: any, j: number) => <Code key={`s${j}`} text={s.sql} lang="sql" />)}
                  {m.tables && <div className="font-mono text-micro text-muted">tables {Object.entries(m.tables).map(([t, o]) => `${t}(${(o as string[]).join("")})`).join(", ")}</div>}
                </div>
              )}
            </li>
          );
        })}
      </ul>
    </Section>
  );
}

function TableBody({ item, d, nav, analysisId }: { item: CatalogItem; d: Details; nav: Nav; analysisId: string }) {
  return (
    <>
      <Section title="Table" right={<span>{d.sources?.join(" · ")}</span>}>
        <KV rows={[["Table", `${d.schema ? d.schema + "." : ""}${d.table}`], ["Entity", d.entity ? <button type="button" className="link" onClick={() => nav.onOpenFile(d.entity_path, d.entity_line)}>{d.entity}</button> : ""], ["ORM", d.orm], ["Dialect", d.dialect], ["Databases", <Chips values={d.databases ?? []} />], ["Comment", d.comment], ["Seed rows", d.seed_rows || ""], ["Engine / tablespace", [d.engine, d.tablespace].filter(Boolean).join(" · ")]]} />
        {d.ddl?.length > 0 && <div className="mt-2 flex flex-wrap gap-2">{d.ddl.map((x: any) => <button key={x.path + x.line} type="button" className="link font-mono text-micro" onClick={() => nav.onOpenFile(x.path, x.line)}>DDL {x.path.split("/").slice(-3).join("/")}:{x.line}</button>)}</div>}
      </Section>
      <Section title="Columns" right={<span>{d.columns?.length ?? 0}</span>}>
        <Table head={["Column", "SQL type", "Entity field", "PK", "Null", "Default", "References", "Rules / comment"]} rows={(d.columns ?? []).map((c: any) => [
          <span className={c.pk ? "text-warning" : ""}>{c.name}</span>, <span className="text-primary">{c.type || "—"}{c.length ? ` (${c.length})` : ""}</span>,
          c.field ? `${c.field}: ${c.java_type ?? ""}` : "", c.pk ? "PK" : "", c.nullable === false ? "NOT NULL" : "", c.default ?? (c.generated ? `generated ${c.generated}` : ""),
          c.references?.table ? `${c.references.table}${c.references.columns?.length ? `(${c.references.columns.join(",")})` : ""}` : (c.references_entity ?? ""),
          <span className="text-muted">{[...(c.validations ?? []), c.comment].filter(Boolean).join(" · ")}</span>,
        ])} />
      </Section>
      <Section title="Relationships" empty={!d.relations?.length && !d.foreign_keys?.length}>
        <Table head={["Field / FK", "Kind", "Target", "Join"]} rows={[
          ...(d.relations ?? []).map((r: any) => [r.field, r.kind, r.target, r.join_column || r.join_table || (r.mapped_by ? `mappedBy ${r.mapped_by}` : "")]),
          ...(d.foreign_keys ?? []).filter((f: any) => !f.from).map((f: any) => [f.name || "FK", "foreign key", `${f.ref_table}(${(f.ref_columns ?? []).join(",")})`, (f.columns ?? []).join(", ")]),
        ]} />
        <div className="mt-2"><ErdPanel analysisId={analysisId} keys={item.item_key} /></div>
      </Section>
      <Section title="Indexes & constraints" empty={!d.indexes?.length && !d.unique?.length && !d.checks?.length}>
        <Table head={["Name", "Columns", "Type"]} rows={[...(d.indexes ?? []).map((x: any) => [x.name || "—", x.columns.join(", "), x.unique ? "unique index" : "index"]), ...(d.unique ?? []).map((x: any) => [x.name || "—", (x.columns ?? []).join(", "), "unique"]), ...(d.checks ?? []).map((c: string) => ["check", c, ""])]} />
      </Section>
      <Section title="CRUD — who reads and writes this table" empty={!d.crud || !Object.keys(d.crud).length}>
        <div className="grid grid-cols-1 gap-2 md:grid-cols-2">
          {Object.entries(d.crud ?? {}).map(([op, names]) => (
            <div key={op} className="border border-line p-2">
              <div className="font-mono text-micro text-primary">{({ C: "CREATE", R: "READ", U: "UPDATE", D: "DELETE" } as Record<string, string>)[op]}</div>
              <ul className="mt-1 space-y-0.5">{(names as string[]).map((n) => <li key={n} className="truncate font-mono text-code"><span className="text-faint">{n.split(":")[0]} </span>{n.split(":").slice(1).join(":")}</li>)}</ul>
            </div>
          ))}
        </div>
      </Section>
    </>
  );
}

function ScreenBody({ d, line }: { d: Details; line: (n: number) => void }) {
  return (
    <>
      <Section title="Screen">
        <KV rows={[["Platform · type", `${d.platform} · ${d.type}`], ["Title", d.title], ["Routes", <Chips values={d.routes ?? []} className="border-layer-ui/40 text-ink" />], ["Rendered by", (d.rendered_by ?? []).join(", ")],
          ["Layout / storyboard", d.layout || d.storyboard], ["View models", <Chips values={d.view_models ?? []} />], ["Components", (d.components ?? []).map((c: any) => c.name).join(", ")],
          ["Launcher", d.launcher ? "yes (MAIN / LAUNCHER)" : ""], ["Deep links", (d.deep_links ?? []).map((x: any) => `${x.scheme}://${x.host ?? ""}${x.path ?? x.pathPrefix ?? ""}`).join(", ")]]} />
      </Section>
      <Section title="Fields" right={<span>{d.fields?.length ?? 0}</span>} empty={!d.fields?.length}>
        <Table head={["Field", "Widget", "Type", "Label", "Required", "Client validation", "Server validation"]} rows={(d.fields ?? []).map((f: any) => [
          <button type="button" className="hover:text-primary" onClick={() => f.line && line(f.line)}>{f.name}</button>, <span className="text-muted">{f.widget}{f.section ? ` · ${f.section}` : ""}</span>, f.type, f.label,
          f.required ? <span className="text-warning">yes</span> : "", <Validations values={[...(f.validations ?? []), ...(f.validation ? [f.validation] : [])]} />,
          f.server_validations?.length ? <div title={f.bound_to}><Validations values={f.server_validations} /></div> : "",
        ])} />
      </Section>
      <Section title="Actions" empty={!d.actions?.length && !d.forms?.length}>
        <Table head={["Label", "Widget", "Handler / submit", "Enabled when"]} rows={[
          ...(d.actions ?? []).map((a: any) => [a.label, a.widget + (a.kind ? ` (${a.kind})` : ""), <span className="text-muted">{a.handler}</span>, a.enabled || a.disabled || ""]),
          ...(d.forms ?? []).map((f: any) => ["form", f.method || "", f.action || f.on_submit || "(posts to the current URL)", f.object ? `binds ${f.object}` : ""]),
        ]} />
      </Section>
      <Section title="Backend calls" right={<span>{d.backend_calls?.length ?? 0}</span>} empty={!d.backend_calls?.length}>
        <Table head={["Method", "URL", "Via", "Backend endpoint"]} rows={(d.backend_calls ?? []).map((c: any) => [
          <MethodBadge method={c.method} />, c.url, <span className="text-muted">{c.via}{c.function ? ` · ${c.function}()` : ""}{c.component ? ` · ${c.component}` : ""}</span>,
          c.endpoint ? <span className="text-ok">{c.endpoint.replace(/^api:/, "")}</span> : <span className="text-faint">not in this repo</span>,
        ])} />
      </Section>
      <Section title="Navigation" empty={!d.navigation?.length}>
        <Table head={["Target", "Via", "Line"]} rows={(d.navigation ?? []).map((n: any) => [n.target, n.via, n.line ? `L${n.line}` : ""])} />
      </Section>
      <Section title="State" empty={!d.state?.length}>
        <Chips values={(d.state ?? []).map((s: any) => (typeof s === "string" ? s : `${s.wrapper ? "@" + s.wrapper + " " : ""}${s.name}${s.from ? " ← " + s.from : ""}`))} />
      </Section>
      <Rules rules={d.rules} llm={d.llm_rules} onLine={line} />
    </>
  );
}

function ConfigBody({ d, nav }: { d: Details; nav: Nav }) {
  return (
    <>
      <Section title="Configuration key" right={d.secret ? <span className="text-critical">secret · masked</span> : <span>{d.category}</span>}>
        <KV rows={[["Key", d.key], ["Description", d.description], ["Environment variables", <Chips values={d.env_vars ?? []} />]]} />
        <div className="mt-2"><Table head={["Profile", "Value", "File"]} rows={(d.values ?? []).map((v: any) => [v.profile, <span className="text-primary">{v.value}</span>, <button type="button" className="link" onClick={() => nav.onOpenFile(v.path, v.line)}>{v.path}:{v.line}</button>])} /></div>
      </Section>
      <Section title="Read by" empty={!d.used_by?.length}>
        <Table head={["File", "Line", "Via"]} rows={(d.used_by ?? []).map((u: any) => [<button type="button" className="link" onClick={() => nav.onOpenFile(u.path, u.line)}>{u.path}</button>, u.line, u.via])} />
      </Section>
    </>
  );
}

function DatabaseBody({ d, nav }: { d: Details; nav: Nav }) {
  return (
    <>
      <Section title="Database technology">
        <KV rows={[["Engine", d.engine], ["Category", d.category], ["Drivers", <Chips values={d.drivers ?? []} />], ["Dialects", (d.dialects ?? []).join(", ")], ["ORM / access", <Chips values={d.orm ?? []} />],
          ["Schema management", (d.ddl_auto ?? []).map((x: any) => `${x.profile}: ddl-auto=${x.value}`).join(" · ")], ["SQL init", (d.sql_init ?? []).map((x: any) => `${x.profile}: ${x.key.split(".").pop()}=${x.value}`).join(" · ")],
          ["Migrations", d.migrations?.tools?.length ? `${d.migrations.tools.join(", ")} · ${d.migrations.files.length} files` : d.migrations?.files?.length ? `${d.migrations.files.length} scripts` : ""],
          ["Room", (d.room_databases ?? []).map((r: any) => `${r.class} v${r.version} ${r.name}`).join(", ")]]} />
      </Section>
      <Section title="Connections" empty={!d.connections?.length}>
        <Table head={["Profile", "URL", "User", "Defined in"]} rows={(d.connections ?? []).map((c: any) => [c.profile, <span className="text-primary">{c.url}</span>, c.username, <button type="button" className="link" onClick={() => nav.onOpenFile(c.path, c.line)}>{c.path}</button>])} />
      </Section>
      <Section title="Evidence" right={<span>{d.evidence?.length ?? 0}</span>}>
        <Table head={["Source", "Detail", "File"]} rows={(d.evidence ?? []).map((e: any) => [e.source, e.detail, e.path ? <button type="button" className="link" onClick={() => nav.onOpenFile(e.path, e.line || undefined)}>{e.path}</button> : ""])} />
      </Section>
      <Section title="Schema files & migrations" empty={!d.schema_files?.length && !d.migrations?.files?.length}>
        <ul className="space-y-0.5">{[...(d.migrations?.files ?? []).map((m: any) => ({ path: m.path, label: `${m.version ? "V" + m.version + " " : ""}${m.description}` })), ...(d.schema_files ?? []).filter((p: string) => !(d.migrations?.files ?? []).some((m: any) => m.path === p)).map((p: string) => ({ path: p, label: p.split("/").pop() }))].map((x) => (
          <li key={x.path}><button type="button" className="link font-mono text-code" onClick={() => nav.onOpenFile(x.path)}>{x.label}</button> <span className="font-mono text-micro text-faint">{x.path}</span></li>
        ))}</ul>
      </Section>
      <Section title="Sequences" empty={!d.sequences?.length}><Chips values={(d.sequences ?? []).map((s: any) => s.name)} /></Section>
    </>
  );
}

function GenericBody({ item, d, line }: { item: CatalogItem; d: Details; line: (n: number) => void }) {
  switch (item.kind) {
    case "service":
    case "view_model":
    case "repository":
      return (
        <>
          <Section title="Overview">
            <KV rows={[["Stereotype", d.stereotype], ["Entity · table", [d.entity, d.table].filter(Boolean).join(" · ")], ["Base", d.base], ["Implements", <Chips values={d.implements ?? []} />],
              ["Dependencies", <Chips values={d.dependencies ?? []} />], ["Transactional", d.transactional ? "class-level @Transactional" : ""], ["Inherited methods", (d.inherited_methods ?? []).join(", ")],
              ["Schedule", d.schedule && Object.keys(d.schedule).length ? JSON.stringify(d.schedule) : ""], ["Mapper XML", d.mapper_xml]]} />
          </Section>
          {d.state?.length > 0 && <Section title="Exposed state"><Chips values={d.state.map((s: any) => `${s.name}: ${s.type}`)} /></Section>}
          <MethodsTable methods={d.methods} line={line} />
          <Rules rules={[]} llm={d.llm_rules} onLine={line} />
          <Section title="Events & outbound calls" empty={!d.events?.length && !d.http_calls?.length}>
            <Table head={["Kind", "Target", "Line"]} rows={[...(d.events ?? []).map((e: any) => [e.kind, e.name, `L${e.line}`]), ...(d.http_calls ?? []).map((h: any) => [`${h.client} ${h.method}`, h.url, `L${h.line}`])]} />
          </Section>
        </>
      );
    case "job":
    case "listener":
      return (
        <>
          <Section title={item.kind === "job" ? "Schedule" : "Subscription"}>
            <KV rows={[["When", d.human || (d.schedule && Object.entries(d.schedule).map(([k, v]) => `${k}=${v}`).join(" · "))], ["Trigger", d.trigger || d.job_type], ["Transport", d.transport], ["Destinations", <Chips values={d.destinations ?? []} />], ["Group", d.group], ["Event type", d.event_type], ["Transactional", d.transactional ? "yes" : ""], ["Lock", d.lock], ["Calls", (d.calls ?? []).join(", ")]]} />
          </Section>
          <MethodsTable methods={d.methods} line={line} />
          <Rules rules={d.rules} llm={d.llm_rules} onLine={line} />
        </>
      );
    case "query":
    case "view":
    case "procedure":
    case "trigger":
      return (
        <>
          <Section title="Statement" right={<span>{[d.dialect, d.operation, d.routine_type, d.modifying ? "modifying" : ""].filter(Boolean).join(" · ")}</span>}>
            <KV rows={[["Owner", d.repository ? `${d.repository}.${d.method}` : ""], ["Tables", <Chips values={Object.entries(d.tables ?? {}).map(([t, o]) => `${t} ${(o as string[]).join("")}`)} className="border-layer-data/40 text-ink" />],
              ["Parameters", (d.params ?? []).map((p: any) => `${p.direction ? p.direction + " " : ""}${p.name}${p.type ? ": " + p.type : ""}`).join(", ")], ["Returns", d.returns], ["Calls procedure", d.procedure], ["Timing", d.timing ? `${d.timing} ${d.events} ON ${d.table}` : ""], ["Members", (d.members ?? []).join(", ")]]} />
            <div className="mt-2"><Code text={d.sql} lang="sql" /></div>
            {d.dialect === "derived" && <p className="mt-1 font-mono text-micro text-faint">Derived from the Spring Data method name; column names follow the entity mapping.</p>}
          </Section>
          <Rules rules={[]} llm={d.llm_rules} onLine={line} />
        </>
      );
    case "api_client":
    case "integration":
      return (
        <Section title="Client call">
          <KV rows={[["Method · path", <span className="flex items-center gap-2"><MethodBadge method={d.method} />{d.path}</span>], ["Client", `${d.client}${d.operation ? "." + d.operation : ""}`], ["Base URL", d.base_url], ["Target service", d.target],
            ["Parameters", (d.params ?? []).map((p: any) => `${p.in}:${p.name}`).join(", ")], ["Request", d.request?.name], ["Response", d.response?.type],
            ["Backend endpoint", d.endpoint ? <span className="text-ok">{d.endpoint.replace(/^api:/, "")}</span> : <span className="text-faint">not found in this repository</span>]]} />
          {d.request?.fields?.length > 0 && <div className="mt-2"><Table head={["Field", "Type", "", "Validation"]} rows={FieldRows(d.request.fields)} /></div>}
        </Section>
      );
    case "dto":
    case "config_class":
      return (
        <Section title={item.kind === "dto" ? "Fields" : `Properties (prefix ${d.prefix})`} right={<span>{d.declared_as}</span>}>
          <Table head={["Field", "Type", "", "Validation"]} rows={FieldRows(d.fields ?? [])} />
        </Section>
      );
    case "enum":
      return <Section title="Values" right={<span>{d.values?.length}</span>}><Chips values={d.values ?? []} className="border-line-strong text-ink" /></Section>;
    case "exception":
      return <Section title="Exception"><KV rows={[["Extends", (d.extends ?? []).join(", ")], ["HTTP status", d.status], ["Handled by", d.handled_by], ["Reason", d.reason]]} /></Section>;
    case "security":
      return (
        <>
          <Section title="Mechanisms"><KV rows={[["Authentication", <Chips values={d.mechanisms ?? []} className="border-warning/40 text-ink" />], ["Filters", (d.filters ?? []).join(", ")], ["Password encoder", (d.password_encoder ?? []).join(", ")], ["Method security", (d.method_security ?? []).join(", ")], ["User details", (d.user_details ?? []).join(", ")]]} /></Section>
          <Section title="Request rules (first match wins)">
            <Table head={["#", "Pattern", "Method", "Access", "Roles", "Line"]} rows={(d.rules ?? []).map((r: any, i: number) => [i + 1, r.patterns.join(", "), r.method || "any", <span className={r.access === "permitAll" ? "text-ok" : r.access === "denyAll" ? "text-critical" : "text-warning"}>{r.access}</span>, (r.roles ?? []).join(", "), <button type="button" className="link" onClick={() => line(r.line)}>L{r.line}</button>])} />
          </Section>
        </>
      );
    case "manifest":
      return (
        <>
          <Section title="App"><KV rows={[["Package", d.package], ["Application", d.application], ["Permissions", <Chips values={d.permissions ?? []} />], ["Cleartext traffic", d.uses_cleartext]]} /></Section>
          <Section title="Components"><Table head={["Type", "Name", "Exported", "Launcher", "Intent filters"]} rows={(d.components ?? []).map((c: any) => [c.type, c.name, c.exported, c.launcher ? "yes" : "", (c.intent_filters ?? []).map((f: any) => f.actions.join(",")).join(" · ")])} /></Section>
        </>
      );
    case "constants":
      return <Section title="Constants"><Table head={["Name", "Type", "Value"]} rows={(d.constants ?? []).map((c: any) => [c.name, c.type, <span className="text-primary">{c.value}</span>])} /></Section>;
    case "utility":
    case "mapper":
      return <MethodsTable methods={d.methods} line={line} />;
    case "bean_config":
      return (
        <Section title="Beans">
          <KV rows={[["Enables", <Chips values={d.enables ?? []} />], ["Profile", d.profile], ["Imports", (d.imports ?? []).join(", ")]]} />
          <div className="mt-2"><Table head={["Bean", "Type", "Profile / conditions", "Line"]} rows={(d.beans ?? []).map((b: any) => [b.name, b.type, [b.profile, ...(b.conditional ?? [])].filter(Boolean).join(" · "), <button type="button" className="link" onClick={() => line(b.line)}>L{b.line}</button>])} /></div>
        </Section>
      );
    case "topic":
      return <Section title="Topic"><KV rows={[["Transport", d.transport], ["Name", d.name]]} /></Section>;
    default:
      return <Section title="Details"><Code text={JSON.stringify(d, null, 2)} /></Section>;
  }
}

export function ItemDetail({ analysisId, itemId, nav }: { analysisId: string; itemId: string; nav: Nav }) {
  const [item, setItem] = useState<CatalogItem | null>(null);
  const [error, setError] = useState("");
  const [showTrace, setShowTrace] = useState(false);
  useEffect(() => {
    setItem(null);
    setError("");
    api<CatalogItem>(`/api/analyses/${analysisId}/catalog/items/${itemId}`).then(setItem).catch((e) => setError(errorText(e)));
  }, [analysisId, itemId]);
  if (error) return <div className="p-3"><ErrorNote message={error} /></div>;
  if (!item) return <div className="p-3"><Spinner /></div>;
  const d = item.details;
  const line = (n: number) => nav.onOpenFile(item.path, n);
  return (
    <div className="space-y-3 p-3">
      <header className="panel p-3">
        <div className="flex flex-wrap items-center gap-2">
          <LayerPill layer={item.layer} />
          <span className="chip border-line-strong text-muted"><span className="material-symbols-outlined icon-sm">{KIND_ICON[item.kind] ?? "circle"}</span>{item.kind.replace("_", " ")}</span>
          {item.framework && <span className="chip border-line-strong text-muted">{item.framework}</span>}
          <div className="ml-auto flex gap-1.5">
            <button type="button" className={`btn h-7 ${showTrace ? "btn-primary" : ""}`} onClick={() => setShowTrace((s) => !s)}><span className="material-symbols-outlined icon-sm">route</span>Trace flow</button>
            {item.path && !item.path.startsWith("catalog/") && <button type="button" className="btn h-7" onClick={() => nav.onOpenFile(item.path, item.line_start || undefined, item.line_end || undefined)}><span className="material-symbols-outlined icon-sm">code</span>Open code</button>}
          </div>
        </div>
        <h2 className="mt-2 break-all font-mono text-head-md">{item.kind === "endpoint" ? <span className="flex items-center gap-2"><MethodBadge method={d.method} />{d.path}</span> : item.name}</h2>
        {item.signature && <p className="mt-1 break-all font-mono text-micro text-muted">{item.signature}</p>}
        {item.path && <p className="mt-1 font-mono text-micro text-faint">{item.path}{item.line_start ? `:${item.line_start}` : ""}</p>}
        {item.summary ? (
          <p className="mt-2 border-l-2 border-primary pl-2 text-body-md">
            {item.summary}
            <span className="ml-2 chip border-primary/40 text-primary" title={item.summary_source === "llm" ? "Explained by the LLM from the code" : "From the OpenAPI specification"}>{item.summary_source === "llm" ? "AI" : item.summary_source}</span>
          </p>
        ) : null}
        {d.llm_notes && <p className="mt-1 text-body-sm text-muted">{d.llm_notes}</p>}
      </header>
      {showTrace && <Section title="End-to-end trace" right={<span>screen → API → service → repository → table</span>}><TracePanel analysisId={analysisId} itemId={item.item_id} /></Section>}
      {item.kind === "endpoint" && <EndpointBody d={d} nav={nav} line={line} />}
      {item.kind === "table" && <TableBody item={item} d={d} nav={nav} analysisId={analysisId} />}
      {(item.kind === "screen" || item.kind === "component") && <ScreenBody d={d} line={line} />}
      {item.kind === "config" && <ConfigBody d={d} nav={nav} />}
      {item.kind === "database" && <DatabaseBody d={d} nav={nav} />}
      {!["endpoint", "table", "screen", "component", "config", "database"].includes(item.kind) && <GenericBody item={item} d={d} line={line} />}
      <LinkGroups title="Depends on / calls" links={item.links_out} nav={nav} />
      <LinkGroups title="Used by" links={item.links_in} nav={nav} />
    </div>
  );
}
