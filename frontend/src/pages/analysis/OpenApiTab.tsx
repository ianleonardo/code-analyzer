import { useEffect, useState } from "react";
import { api, downloadFile, errorText } from "../../api";
import { useDebounced } from "../../components/ListTools";
import { SpecDiffTable, SpecViewer, type SpecDiff, type SpecResult } from "../../components/openapi/SpecViewer";
import { Empty, ErrorNote, Select, Spinner } from "../../components/ui";
import type { Analysis } from "../../types";

type Overview = {
  applicable: boolean; message: string; llm: boolean; spec_files: string[];
  services: { name: string; path: string; ecosystem: string; endpoints: number; spec_only: number; frameworks: string[] }[];
};
type Options = { service: string; version: string; format: string; tag_by: string; include_internal: boolean; extensions: boolean; enrich: boolean };

function query(options: Options, extra: Record<string, string> = {}) {
  const qs = new URLSearchParams({
    service: options.service, version: options.version, format: options.format, tag_by: options.tag_by,
    include_internal: String(options.include_internal), extensions: String(options.extensions), enrich: String(options.enrich), ...extra,
  });
  return qs.toString();
}

/** OpenAPI for an analysis: options, built-in preview, drift against a committed spec, and downloads. */
export function OpenApiTab({ analysis, onOpenFile }: { analysis: Analysis; onOpenFile: (path: string, line?: number) => void }) {
  const base = `/api/analyses/${analysis.analysis_id}/openapi`;
  const [info, setInfo] = useState<Overview | null>(null);
  const [options, setOptions] = useState<Options>({ service: "", version: "3.0.3", format: "yaml", tag_by: "controller", include_internal: false, extensions: true, enrich: false });
  const [result, setResult] = useState<SpecResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [mode, setMode] = useState<"spec" | "drift">("spec");
  const [drift, setDrift] = useState<(SpecDiff & { spec_path: string; spec_files: string[]; swagger2: boolean }) | null>(null);
  const [driftPath, setDriftPath] = useState("");
  const [error, setError] = useState("");
  const settled = useDebounced(options, 250);

  useEffect(() => {
    api<Overview>(base).then((data) => {
      setInfo(data);
      if (data.services[0]) setOptions((o) => ({ ...o, service: o.service || [...data.services].sort((a, b) => b.endpoints - a.endpoints)[0].name }));
    }).catch((err) => setError(errorText(err)));
  }, [base]);

  useEffect(() => {
    if (!info?.applicable || !settled.service) return;
    setLoading(true);
    setError("");
    api<SpecResult>(`${base}/spec?${query(settled)}`).then(setResult).catch((err) => setError(errorText(err))).finally(() => setLoading(false));
  }, [base, info, settled]);

  useEffect(() => {
    if (mode !== "drift" || !info?.spec_files.length) return;
    setDrift(null);
    api<SpecDiff & { spec_path: string; spec_files: string[]; swagger2: boolean }>(`${base}/drift?${new URLSearchParams({ spec_path: driftPath, service: options.service })}`)
      .then(setDrift).catch((err) => setError(errorText(err)));
  }, [mode, base, info, driftPath, options.service]);

  const set = <K extends keyof Options>(key: K, value: Options[K]) => setOptions((o) => ({ ...o, [key]: value }));
  const save = (extra: Record<string, string>, name: string) => downloadFile(`${base}/download?${query(options, extra)}`, name).catch((err) => setError(errorText(err)));
  const ext = options.format === "json" ? "json" : "yaml";
  const openSource = (source: string) => {
    const m = source.match(/^(.*):(\d+)$/);
    onOpenFile(m ? m[1] : source, m ? Number(m[2]) : undefined);
  };

  if (!info) return <div className="p-4">{error ? <ErrorNote message={error} /> : <Spinner label="Looking for REST endpoints…" />}</div>;
  if (!info.applicable) return <div className="p-4"><section className="panel"><Empty icon="api" title="No OpenAPI for this analysis">{info.message}</Empty></section></div>;

  return (
    <div className="space-y-3 p-4">
      <section className="panel p-3">
        <div className="flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="label-micro">Service</span>
            <Select className="field mt-1 w-56" value={options.service} onChange={(e) => { set("service", e.target.value); setSelected(new Set()); }}>
              {info.services.map((s) => <option key={s.name} value={s.name}>{s.name} · {s.endpoints} endpoint(s)</option>)}
            </Select>
          </label>
          <label className="block">
            <span className="label-micro">OpenAPI</span>
            <Select className="field mt-1 w-28" value={options.version} onChange={(e) => set("version", e.target.value)}>
              <option value="3.0.3">3.0.3</option>
              <option value="3.1.0">3.1</option>
            </Select>
          </label>
          <label className="block">
            <span className="label-micro">Format</span>
            <Select className="field mt-1 w-24" value={options.format} onChange={(e) => set("format", e.target.value)}>
              <option value="yaml">YAML</option>
              <option value="json">JSON</option>
            </Select>
          </label>
          <label className="block">
            <span className="label-micro">Group (tags) by</span>
            <Select className="field mt-1 w-40" value={options.tag_by} onChange={(e) => set("tag_by", e.target.value)}>
              <option value="controller">Controller</option>
              <option value="feature">Feature catalogue</option>
              <option value="path">Path segment</option>
            </Select>
          </label>
          <div className="flex flex-col gap-1 pb-1 text-body-sm text-muted">
            <label className="flex items-center gap-1"><input type="checkbox" checked={options.include_internal} onChange={(e) => set("include_internal", e.target.checked)} />Include actuator / health / error endpoints</label>
            <label className="flex items-center gap-1"><input type="checkbox" checked={options.extensions} onChange={(e) => set("extensions", e.target.checked)} />Include x- extensions (source, roles, inferred)</label>
            <label className={`flex items-center gap-1 ${info.llm ? "" : "opacity-50"}`} title={info.llm ? "" : "Configure an LLM under Admin › LLM"}>
              <input type="checkbox" disabled={!info.llm} checked={options.enrich} onChange={(e) => set("enrich", e.target.checked)} />AI descriptions &amp; examples (structure unchanged)
            </label>
          </div>
          {info.spec_files.length > 0 && (
            <div className="ml-auto flex gap-1 self-end">
              <button type="button" className={`btn ${mode === "spec" ? "border-primary bg-primary/15 text-primary" : ""}`} onClick={() => setMode("spec")}>Generated spec</button>
              <button type="button" className={`btn ${mode === "drift" ? "border-primary bg-primary/15 text-primary" : ""}`} onClick={() => setMode("drift")}>
                <span className="material-symbols-outlined icon-sm">compare</span>Drift vs committed spec
              </button>
            </div>
          )}
        </div>
        {info.spec_files.length > 0 && mode === "spec" && (
          <p className="mt-2 text-body-sm text-warning">
            This repository commits an OpenAPI file ({info.spec_files.join(", ")}). Operations only in that file are marked “in the spec only”; use “Drift vs committed spec” to see where code and spec disagree.
          </p>
        )}
      </section>
      <ErrorNote message={error} />
      {mode === "drift" ? (
        <section className="panel">
          <div className="panel-head">
            <span>Committed spec vs code{drift?.swagger2 ? " · Swagger 2.0 converted" : ""}</span>
            {info.spec_files.length > 1 && (
              <Select className="field h-6 w-72 normal-case" value={driftPath || drift?.spec_path || ""} onChange={(e) => setDriftPath(e.target.value)}>
                {info.spec_files.map((f) => <option key={f} value={f}>{f}</option>)}
              </Select>
            )}
          </div>
          {!drift ? <div className="p-3"><Spinner label="Comparing…" /></div> : <SpecDiffTable diff={drift} empty="The code implements exactly what the committed spec describes." />}
        </section>
      ) : (
        <>
          {loading && !result && <Spinner label="Generating…" />}
          {result && (
            <div className={loading ? "opacity-60" : ""}>
              <SpecViewer
                result={result}
                selectable
                selected={selected}
                onSelectedChange={setSelected}
                onOpenSource={openSource}
                toolbar={
                  <>
                    <button type="button" className="btn h-7" title="Copy to clipboard" onClick={() => void navigator.clipboard?.writeText(result.text)}>
                      <span className="material-symbols-outlined icon-sm">content_copy</span>Copy
                    </button>
                    <button type="button" className="btn btn-primary h-7" onClick={() => void save({}, result.filename)}>
                      <span className="material-symbols-outlined icon-sm">download</span>{ext.toUpperCase()}
                    </button>
                    <button type="button" className="btn h-7" onClick={() => void save({ format: ext === "yaml" ? "json" : "yaml" }, result.filename.replace(/\.(yaml|json)$/, ext === "yaml" ? ".json" : ".yaml"))}>
                      {ext === "yaml" ? "JSON" : "YAML"}
                    </button>
                    {selected.size > 0 && (
                      <button type="button" className="btn h-7" onClick={() => void save({ keys: [...selected].join(",") }, result.filename.replace(/(\.\w+)$/, `-selected$1`))}>
                        Selected ({selected.size})
                      </button>
                    )}
                    {info.services.length > 1 && (
                      <button type="button" className="btn h-7" title="One spec per service in a zip" onClick={() => void save({ service: "*" }, `${analysis.repo_name ?? "openapi"}-all-services.zip`)}>
                        <span className="material-symbols-outlined icon-sm">folder_zip</span>All services
                      </button>
                    )}
                  </>
                }
              />
            </div>
          )}
        </>
      )}
    </div>
  );
}
