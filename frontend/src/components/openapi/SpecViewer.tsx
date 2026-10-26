import { useMemo, useState, type ReactNode } from "react";
import { CodeView } from "../CodeView";
import { Empty, Markdown } from "../ui";

/* eslint-disable @typescript-eslint/no-explicit-any -- an OpenAPI document is free-form JSON */
export type Spec = Record<string, any>;
export type SpecOperation = {
  operation_id: string; method: string; path: string; tags?: string[]; summary?: string; item_key?: string; source?: string;
  completeness?: number; inferred?: number; spec_only?: boolean; deprecated?: boolean; consumer?: string;
};
export type SpecWarning = { level: "error" | "warning" | "info"; operation: string; message: string };
export type SpecResult = {
  spec: Spec; text: string; operations: SpecOperation[]; warnings: SpecWarning[]; completeness: number | null; valid: boolean; filename: string; service: string;
  services?: string[]; stats: { operations: number; schemas: number; inferred: number; fields: number }; options: Record<string, unknown>;
};

export const METHOD_COLOR: Record<string, string> = {
  GET: "text-ok border-ok/50 bg-ok/10",
  POST: "text-primary border-primary/50 bg-primary/10",
  PUT: "text-warning border-warning/50 bg-warning/10",
  PATCH: "text-layer-service border-layer-service/50 bg-layer-service/10",
  DELETE: "text-critical border-critical/50 bg-critical/10",
};

export function MethodBadge({ method }: { method: string }) {
  return <span className={`chip w-14 justify-center ${METHOD_COLOR[method.toUpperCase()] ?? "border-line text-muted"}`}>{method.toUpperCase()}</span>;
}

function resolveRef(spec: Spec, ref: string): [string, any] {
  const name = ref.split("/").pop() ?? ref;
  return [name, spec.components?.schemas?.[name] ?? {}];
}

function typeLabel(spec: Spec, schema: any): string {
  if (!schema || typeof schema !== "object") return "any";
  if (schema.$ref) return resolveRef(spec, schema.$ref)[0];
  if (schema.allOf?.length === 1) return typeLabel(spec, schema.allOf[0]);
  if (schema.anyOf) return schema.anyOf.map((s: any) => typeLabel(spec, s)).join(" | ");
  const t = Array.isArray(schema.type) ? schema.type.filter((x: string) => x !== "null").join("|") : schema.type;
  if (t === "array") return `${typeLabel(spec, schema.items)}[]`;
  if (t === "object" && schema.additionalProperties) return `map<string, ${typeLabel(spec, schema.additionalProperties)}>`;
  return `${t ?? "any"}${schema.format ? ` (${schema.format})` : ""}`;
}

function constraints(schema: any): string[] {
  if (!schema || typeof schema !== "object") return [];
  const out: string[] = [];
  const keys: [string, string][] = [["minLength", "min length"], ["maxLength", "max length"], ["minimum", "≥"], ["maximum", "≤"], ["minItems", "min items"], ["maxItems", "max items"],
    ["pattern", "pattern"], ["default", "default"]];
  for (const [k, label] of keys) if (schema[k] !== undefined && schema[k] !== true) out.push(`${label} ${JSON.stringify(schema[k])}`);
  if (schema.exclusiveMinimum !== undefined) out.push(schema.exclusiveMinimum === true ? "exclusive min" : `> ${schema.exclusiveMinimum}`);
  if (schema.exclusiveMaximum !== undefined) out.push(schema.exclusiveMaximum === true ? "exclusive max" : `< ${schema.exclusiveMaximum}`);
  if (schema.enum) out.push(`one of ${schema.enum.slice(0, 12).join(", ")}${schema.enum.length > 12 ? "…" : ""}`);
  if (schema.nullable || (Array.isArray(schema.type) && schema.type.includes("null"))) out.push("nullable");
  for (const v of schema["x-validations"] ?? []) out.push(String(v));
  return out;
}

/** Expandable schema tree with $ref resolution (cycles stop at the second visit). */
export function SchemaTree({ spec, schema, depth = 0, seen = [] }: { spec: Spec; schema: any; depth?: number; seen?: string[] }) {
  let target = schema ?? {};
  let refName = "";
  if (target.$ref) [refName, target] = resolveRef(spec, target.$ref);
  if (target.allOf?.length === 1 && target.allOf[0].$ref) [refName, target] = resolveRef(spec, target.allOf[0].$ref);
  if (refName && seen.includes(refName)) return <div className="pl-3 font-mono text-micro text-faint">↻ {refName} (recursive)</div>;
  const nextSeen = refName ? [...seen, refName] : seen;
  const t = Array.isArray(target.type) ? target.type.find((x: string) => x !== "null") : target.type;
  if (t === "array") {
    return (
      <div>
        <div className="font-mono text-micro text-faint">array of {typeLabel(spec, target.items)}</div>
        <SchemaTree spec={spec} schema={target.items} depth={depth + 1} seen={nextSeen} />
      </div>
    );
  }
  const props = target.properties as Record<string, any> | undefined;
  if (!props || !Object.keys(props).length) {
    return (
      <div className="font-mono text-micro text-muted">
        {typeLabel(spec, schema)}{target.description ? ` — ${target.description}` : ""}{target["x-inferred"] ? <span className="ml-1 text-warning">· inferred</span> : null}
      </div>
    );
  }
  const required = new Set<string>(target.required ?? []);
  return (
    <table className="w-full">
      <tbody>
        {Object.entries(props).map(([name, prop]) => <SchemaRow key={name} spec={spec} name={name} prop={prop} required={required.has(name)} depth={depth} seen={nextSeen} />)}
      </tbody>
    </table>
  );
}

function SchemaRow({ spec, name, prop, required, depth, seen }: { spec: Spec; name: string; prop: any; required: boolean; depth: number; seen: string[] }) {
  const [open, setOpen] = useState(depth < 1);
  let inner = prop;
  if (prop?.$ref) inner = resolveRef(spec, prop.$ref)[1];
  if (inner?.type === "array" && inner.items) inner = inner.items.$ref ? resolveRef(spec, inner.items.$ref)[1] : inner.items;
  const expandable = !!inner?.properties && Object.keys(inner.properties).length > 0 && depth < 6;
  const notes = constraints(prop?.$ref ? {} : prop);
  return (
    <>
      <tr className="border-b border-line/40 align-top">
        <td className="w-1/3 py-1 pr-2 font-mono text-code" style={{ paddingLeft: depth * 14 }}>
          {expandable ? (
            <button type="button" className="inline-flex items-center hover:text-primary" onClick={() => setOpen(!open)}>
              <span className="material-symbols-outlined text-[14px]">{open ? "expand_more" : "chevron_right"}</span>{name}
            </button>
          ) : <span className="pl-[14px]">{name}</span>}
          {required && <span className="ml-1 text-critical" title="required">*</span>}
        </td>
        <td className="py-1 pr-2 font-mono text-micro text-primary">{typeLabel(spec, prop)}</td>
        <td className="py-1 text-body-sm text-muted">
          {prop?.description && <span>{prop.description} </span>}
          {notes.map((n) => <span key={n} className="mr-1 inline-block rounded-sm border border-line px-1 font-mono text-micro">{n}</span>)}
          {prop?.["x-inferred"] && <span className="chip border-warning/50 text-warning">inferred</span>}
        </td>
      </tr>
      {expandable && open && (
        <tr><td colSpan={3}><SchemaTree spec={spec} schema={prop?.type === "array" ? prop.items : prop} depth={depth + 1} seen={seen} /></td></tr>
      )}
    </>
  );
}

function Section({ title, children, right }: { title: string; children: ReactNode; right?: ReactNode }) {
  return (
    <section className="space-y-2">
      <div className="flex items-center justify-between border-b border-line pb-1"><span className="label-micro">{title}</span>{right}</div>
      {children}
    </section>
  );
}

function OperationDoc({ spec, path, method, op, meta, onOpenSource }: {
  spec: Spec; path: string; method: string; op: any; meta?: SpecOperation; onOpenSource?: (source: string) => void;
}) {
  const params: any[] = op.parameters ?? [];
  const body = op.requestBody;
  const bodyMedia = body ? Object.entries(body.content ?? {})[0] : null;
  return (
    <div className="space-y-4">
      <div>
        <div className="flex flex-wrap items-center gap-2">
          <MethodBadge method={method} />
          <span className="font-mono text-head-md">{path}</span>
          {op.deprecated && <span className="chip border-warning/50 text-warning">deprecated</span>}
          {op["x-spec-only"] && <span className="chip border-warning/50 text-warning">in the spec only — not implemented</span>}
          {meta?.completeness !== undefined && <span className={`chip ${meta.completeness >= 80 ? "border-ok/50 text-ok" : meta.completeness >= 50 ? "border-warning/50 text-warning" : "border-critical/50 text-critical"}`}>{meta.completeness}% complete</span>}
        </div>
        <div className="mt-1 text-head-sm">{op.summary}</div>
        <div className="mt-1 flex flex-wrap items-center gap-2 font-mono text-micro text-faint">
          <span>operationId {op.operationId}</span>
          {(op.tags ?? []).map((t: string) => <span key={t} className="chip border-line text-muted">{t}</span>)}
          {op["x-source"] && (onOpenSource ? <button type="button" className="link" onClick={() => onOpenSource(op["x-source"])}>{op["x-source"]}</button> : <span>{op["x-source"]}</span>)}
          {op["x-enriched-by"] && <span className="chip border-primary/40 text-primary">AI descriptions</span>}
        </div>
        {op.description && <Markdown className="mt-2 text-body-md" text={op.description} />}
        {(op["x-consumers"] ?? []).length > 0 && (
          <div className="mt-2 text-body-sm text-muted">Called by: {op["x-consumers"].map((c: string) => <div key={c} className="font-mono text-micro">{c}</div>)}</div>
        )}
      </div>
      {(op.security !== undefined || op["x-roles"]) && (
        <Section title="Security">
          <div className="flex flex-wrap gap-2 text-body-sm">
            {op.security?.length === 0 && <span className="chip border-ok/50 text-ok">public</span>}
            {(op.security ?? []).flatMap((req: any) => Object.keys(req)).map((s: string) => <span key={s} className="chip border-line text-muted">{s}</span>)}
            {(op["x-roles"] ?? []).map((r: string) => <span key={r} className="chip border-warning/50 text-warning">role {r}</span>)}
          </div>
        </Section>
      )}
      {params.length > 0 && (
        <Section title="Parameters">
          <table className="w-full">
            <thead className="table-head"><tr><th>Name</th><th>In</th><th>Type</th><th>Notes</th></tr></thead>
            <tbody>
              {params.map((p) => (
                <tr key={`${p.in}:${p.name}`} className="table-row">
                  <td className="font-mono text-code">{p.name}{p.required && <span className="ml-1 text-critical">*</span>}</td>
                  <td className="font-mono text-micro text-muted">{p.in}</td>
                  <td className="font-mono text-micro text-primary">{typeLabel(spec, p.schema)}</td>
                  <td className="text-body-sm text-muted">{p.description} {constraints(p.schema).map((n) => <span key={n} className="mr-1 inline-block rounded-sm border border-line px-1 font-mono text-micro">{n}</span>)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </Section>
      )}
      {bodyMedia && (
        <Section title={`Request body${body.required ? " (required)" : ""}`} right={<span className="font-mono text-micro text-faint">{bodyMedia[0]}</span>}>
          <SchemaTree spec={spec} schema={(bodyMedia[1] as any).schema} />
          {(bodyMedia[1] as any).example && <pre className="overflow-auto bg-inset p-2 font-mono text-micro">{JSON.stringify((bodyMedia[1] as any).example, null, 2)}</pre>}
        </Section>
      )}
      <Section title="Responses">
        <div className="space-y-3">
          {Object.entries(op.responses ?? {}).map(([code, resp]: [string, any]) => {
            const media = Object.entries(resp.content ?? {})[0] as [string, any] | undefined;
            return (
              <div key={code} className="border border-line p-2">
                <div className="flex items-center gap-2">
                  <span className={`chip ${code.startsWith("2") ? "border-ok/50 text-ok" : code.startsWith("4") ? "border-warning/50 text-warning" : "border-critical/50 text-critical"}`}>{code}</span>
                  <span className="text-body-md">{resp.description}</span>
                  {media && <span className="ml-auto font-mono text-micro text-faint">{media[0]}</span>}
                </div>
                {media?.[1]?.schema && <div className="mt-2"><SchemaTree spec={spec} schema={media[1].schema} /></div>}
                {media?.[1]?.example && <pre className="mt-2 overflow-auto bg-inset p-2 font-mono text-micro">{JSON.stringify(media[1].example, null, 2)}</pre>}
              </div>
            );
          })}
        </div>
      </Section>
    </div>
  );
}

function SpecOverview({ result }: { result: SpecResult }) {
  const spec = result.spec;
  const schemes = Object.entries(spec.components?.securitySchemes ?? {}) as [string, any][];
  const schemas = Object.keys(spec.components?.schemas ?? {});
  return (
    <div className="space-y-4">
      <div>
        <div className="text-head-lg">{spec.info?.title}</div>
        <div className="font-mono text-micro text-faint">OpenAPI {spec.openapi} · version {spec.info?.version}</div>
        {spec.info?.description && <Markdown className="mt-2 text-body-md" text={spec.info.description} />}
      </div>
      <div className="grid grid-cols-2 gap-2 md:grid-cols-4">
        {[["Operations", result.stats.operations], ["Schemas", result.stats.schemas], ["Completeness", result.completeness === null ? "—" : `${result.completeness}%`], ["Inferred parts", result.stats.inferred]].map(([l, v]) => (
          <div key={String(l)} className="card"><div className="label-micro">{l}</div><div className="kpi mt-1">{v}</div></div>
        ))}
      </div>
      <Section title="Servers">
        {(spec.servers ?? []).map((s: any) => <div key={s.url} className="font-mono text-code">{s.url} <span className="text-micro text-faint">{s.description}</span></div>)}
      </Section>
      {schemes.length > 0 && (
        <Section title="Security schemes">
          {schemes.map(([name, s]) => <div key={name} className="font-mono text-code">{name} <span className="text-micro text-faint">{s.type}{s.scheme ? ` · ${s.scheme}` : ""}{s.bearerFormat ? ` · ${s.bearerFormat}` : ""}{s["x-inferred"] ? " · inferred" : ""}</span></div>)}
        </Section>
      )}
      {schemas.length > 0 && (
        <Section title={`Schemas (${schemas.length})`}>
          <div className="space-y-2">
            {schemas.map((name) => (
              <details key={name} className="border border-line">
                <summary className="cursor-pointer px-2 py-1 font-mono text-code">{name}</summary>
                <div className="border-t border-line p-2"><SchemaTree spec={spec} schema={{ $ref: `#/components/schemas/${name}` }} /></div>
              </details>
            ))}
          </div>
        </Section>
      )}
    </div>
  );
}

/**
 * Built-in OpenAPI viewer: operations tree (grouped by tag, searchable, optionally selectable) beside the rendered
 * docs, the YAML / JSON source and the warnings. Works on any OpenAPI 3.x document (no network calls).
 */
export function SpecViewer({ result, selectable = false, selected, onSelectedChange, onOpenSource, toolbar }: {
  result: SpecResult; selectable?: boolean; selected?: Set<string>; onSelectedChange?: (keys: Set<string>) => void; onOpenSource?: (source: string) => void; toolbar?: ReactNode;
}) {
  const [view, setView] = useState<"docs" | "source" | "warnings">("docs");
  const [current, setCurrent] = useState<string | null>(null);
  const [q, setQ] = useState("");
  const spec = result.spec;
  const metaByKey = useMemo(() => Object.fromEntries(result.operations.map((o) => [`${o.method} ${o.path}`, o])), [result]);
  const groups = useMemo(() => {
    const out: Record<string, { key: string; method: string; path: string; op: any }[]> = {};
    for (const [path, item] of Object.entries(spec.paths ?? {}) as [string, any][]) {
      for (const [method, op] of Object.entries(item) as [string, any][]) {
        const text = `${method} ${path} ${op.summary ?? ""} ${op.operationId ?? ""}`.toLowerCase();
        if (q && !text.includes(q.toLowerCase())) continue;
        for (const tag of op.tags?.length ? op.tags : ["default"]) (out[tag] ??= []).push({ key: `${method.toUpperCase()} ${path}`, method, path, op });
      }
    }
    return Object.entries(out).sort((a, b) => a[0].localeCompare(b[0]));
  }, [spec, q]);
  const active = current ? groups.flatMap(([, ops]) => ops).find((o) => o.key === current) : null;
  const warningsFor = (key: string) => result.warnings.filter((w) => w.operation && metaByKey[key]?.operation_id === w.operation);
  const errors = result.warnings.filter((w) => w.level === "error").length;
  const toggle = (itemKey: string | undefined) => {
    if (!itemKey || !selected || !onSelectedChange) return;
    const next = new Set(selected);
    if (next.has(itemKey)) next.delete(itemKey);
    else next.add(itemKey);
    onSelectedChange(next);
  };

  return (
    <div className="grid min-h-[70vh] gap-0 border border-line xl:grid-cols-[340px_1fr]">
      <aside className="border-b border-line xl:border-b-0 xl:border-r">
        <div className="space-y-2 border-b border-line p-2">
          <input className="field" placeholder="Search operations…" value={q} onChange={(e) => setQ(e.target.value)} />
          {selectable && selected && onSelectedChange && (
            <div className="flex items-center gap-2 text-body-sm text-muted">
              <span>{selected.size ? `${selected.size} selected` : "Tick operations to download a subset"}</span>
              {selected.size > 0 && <button type="button" className="link" onClick={() => onSelectedChange(new Set())}>clear</button>}
            </div>
          )}
        </div>
        <button type="button" className={`block w-full border-b border-line px-3 py-1.5 text-left text-body-md ${!current ? "bg-primary/10 text-primary" : "hover:bg-elevated"}`} onClick={() => setCurrent(null)}>
          <span className="material-symbols-outlined icon-sm mr-1 align-middle">info</span>Overview
        </button>
        <div className="max-h-[65vh] overflow-auto">
          {groups.map(([tag, ops]) => (
            <div key={tag}>
              <div className="sticky top-0 z-[1] bg-surface px-3 pb-0.5 pt-2 font-mono text-micro uppercase text-faint">{tag} · {ops.length}</div>
              {ops.map((o) => {
                const meta = metaByKey[o.key];
                const warn = warningsFor(o.key).some((w) => w.level !== "info");
                return (
                  <div key={`${tag}:${o.key}`} className={`flex items-center gap-2 px-2 py-1 ${current === o.key ? "bg-primary/10" : "hover:bg-elevated"}`}>
                    {selectable && <input type="checkbox" checked={!!meta?.item_key && !!selected?.has(meta.item_key)} disabled={!meta?.item_key} onChange={() => toggle(meta?.item_key)} />}
                    <button type="button" className="flex min-w-0 flex-1 items-center gap-2 text-left" onClick={() => { setCurrent(o.key); setView("docs"); }}>
                      <MethodBadge method={o.method} />
                      <span className={`min-w-0 flex-1 truncate font-mono text-code ${o.op.deprecated ? "line-through text-faint" : ""}`} title={o.op.summary}>{o.path}</span>
                      {warn && <span className="material-symbols-outlined icon-sm text-warning" title="has warnings">warning</span>}
                      {o.op["x-spec-only"] && <span className="material-symbols-outlined icon-sm text-warning" title="in the spec only">description</span>}
                    </button>
                  </div>
                );
              })}
            </div>
          ))}
          {!groups.length && <p className="p-3 text-body-sm text-muted">No operation matches.</p>}
        </div>
      </aside>
      <div className="min-w-0">
        <div className="flex flex-wrap items-center gap-1 border-b border-line px-2">
          {([["docs", "menu_book", "Docs"], ["source", "code", result.options.format === "json" ? "JSON" : "YAML"], ["warnings", "warning", `Warnings (${result.warnings.length})`]] as const).map(([key, icon, label]) => (
            <button key={key} type="button" className={`tab ${view === key ? "tab-active" : ""}`} onClick={() => setView(key)}>
              <span className="material-symbols-outlined icon-sm">{icon}</span>{label}
            </button>
          ))}
          <span className={`ml-2 chip ${result.valid ? "border-ok/50 text-ok" : "border-critical/50 text-critical"}`} title="checked against the core OpenAPI rules">
            {result.valid ? "valid" : `${errors} error(s)`}
          </span>
          <div className="ml-auto flex flex-wrap items-center gap-1 py-1">{toolbar}</div>
        </div>
        <div className="max-h-[75vh] overflow-auto p-4">
          {view === "docs" && (active ? <OperationDoc spec={spec} path={active.path} method={active.method} op={active.op} meta={metaByKey[active.key]} onOpenSource={onOpenSource} /> : <SpecOverview result={result} />)}
          {view === "source" && <div className="h-[68vh] overflow-auto border border-line"><CodeView content={result.text} /></div>}
          {view === "warnings" && (
            result.warnings.length ? (
              <table className="w-full">
                <thead className="table-head"><tr><th>Level</th><th>Operation</th><th>Message</th></tr></thead>
                <tbody>
                  {result.warnings.map((w, i) => (
                    <tr key={i} className="table-row">
                      <td><span className={`chip ${w.level === "error" ? "border-critical/50 text-critical" : w.level === "warning" ? "border-warning/50 text-warning" : "border-line text-muted"}`}>{w.level}</span></td>
                      <td className="font-mono text-code">{w.operation}</td>
                      <td className="text-body-md">{w.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : <Empty icon="task_alt" title="No warnings">Everything in the spec comes straight from typed code.</Empty>
          )}
        </div>
      </div>
    </div>
  );
}

export type SpecChange = { severity: "breaking" | "non-breaking" | "undocumented"; operation: string; where: string; change: string };
export type SpecDiff = { available?: boolean; message?: string; changes: SpecChange[]; breaking: number; non_breaking: number; operations?: { old: number; new: number; added: number; removed: number } };

/** Table of contract changes from a spec diff (breaking first). */
export function SpecDiffTable({ diff, empty = "No API contract changes." }: { diff: SpecDiff; empty?: string }) {
  const [onlyBreaking, setOnlyBreaking] = useState(false);
  const rows = [...diff.changes].sort((a, b) => Number(a.severity !== "breaking") - Number(b.severity !== "breaking") || a.operation.localeCompare(b.operation))
    .filter((c) => !onlyBreaking || c.severity === "breaking");
  if (!diff.changes.length) return <p className="p-3 text-body-md text-muted">{empty}</p>;
  return (
    <div>
      <div className="flex items-center gap-3 border-b border-line px-3 py-2 text-body-sm">
        <span className="chip border-critical/50 text-critical">{diff.breaking} breaking</span>
        <span className="chip border-line text-muted">{diff.non_breaking} other</span>
        <label className="ml-auto flex items-center gap-1 text-muted"><input type="checkbox" checked={onlyBreaking} onChange={(e) => setOnlyBreaking(e.target.checked)} />Breaking only</label>
      </div>
      <table className="w-full">
        <thead className="table-head"><tr><th>Severity</th><th>Operation</th><th>Where</th><th>Change</th></tr></thead>
        <tbody>
          {rows.map((c, i) => (
            <tr key={i} className="table-row">
              <td><span className={`chip ${c.severity === "breaking" ? "border-critical/50 text-critical" : c.severity === "undocumented" ? "border-warning/50 text-warning" : "border-line text-muted"}`}>{c.severity}</span></td>
              <td className="font-mono text-code">{c.operation}</td>
              <td className="font-mono text-micro text-muted">{c.where}</td>
              <td className="text-body-md">{c.change}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
