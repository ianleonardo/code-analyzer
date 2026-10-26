import { useEffect, useState } from "react";
import { api, downloadFile, errorText } from "../../api";
import { ErrorNote, Modal, Select, Spinner } from "../ui";
import { SpecDiffTable, SpecViewer, type SpecDiff, type SpecResult } from "./SpecViewer";

/** Breaking-change check between two generated OpenAPI specs (Compare, PR impact). */
export function OpenApiDiffPanel({ url, title = "API contract (OpenAPI) changes" }: { url: string; title?: string }) {
  const [diff, setDiff] = useState<SpecDiff | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<SpecDiff>(url).then(setDiff).catch((err) => setError(errorText(err)));
  }, [url]);
  return (
    <section className="panel">
      <div className="panel-head">
        <span>{title}</span>
        {diff?.available !== false && diff && <span className={diff.breaking ? "text-critical" : "text-ok"}>{diff.breaking ? `${diff.breaking} breaking` : "no breaking change"}</span>}
      </div>
      <ErrorNote message={error} />
      {!diff && !error && <div className="p-3"><Spinner label="Generating both specs…" /></div>}
      {diff && diff.available === false && <p className="p-3 text-body-md text-muted">{diff.message}</p>}
      {diff && diff.available !== false && (
        <>
          {diff.operations && (
            <p className="border-b border-line px-3 py-2 font-mono text-micro text-faint">
              {diff.operations.old} → {diff.operations.new} operations · +{diff.operations.added} / −{diff.operations.removed}
            </p>
          )}
          <SpecDiffTable diff={diff} empty="The REST contract is unchanged." />
        </>
      )}
    </section>
  );
}

/** OpenAPI draft of an external API as the landscape's repositories use it. */
export function ExternalSpecModal({ landscapeId, name, onClose }: { landscapeId: string; name: string; onClose: () => void }) {
  const [format, setFormat] = useState("yaml");
  const [version, setVersion] = useState("3.0.3");
  const [result, setResult] = useState<SpecResult | null>(null);
  const [error, setError] = useState("");
  const qs = new URLSearchParams({ name, format, version }).toString();
  useEffect(() => {
    api<SpecResult>(`/api/landscapes/${landscapeId}/external-openapi?${qs}`).then(setResult).catch((err) => setError(errorText(err)));
  }, [landscapeId, qs]);
  return (
    <Modal title={<span className="font-mono">{name} · OpenAPI as used</span>} onClose={onClose} width="max-w-7xl">
      <p className="mb-2 text-body-sm text-muted">Drafted from the calls the repositories in this landscape make to {name}: only what is used, with the types their clients declare. Check it against the provider&apos;s own documentation.</p>
      <ErrorNote message={error} />
      {!result && !error && <Spinner label="Drafting…" />}
      {result && (
        <SpecViewer
          result={result}
          toolbar={
            <>
              <Select className="field h-7 w-24" value={version} onChange={(e) => setVersion(e.target.value)}><option value="3.0.3">3.0.3</option><option value="3.1.0">3.1</option></Select>
              <Select className="field h-7 w-24" value={format} onChange={(e) => setFormat(e.target.value)}><option value="yaml">YAML</option><option value="json">JSON</option></Select>
              <button type="button" className="btn btn-primary h-7" onClick={() => void downloadFile(`/api/landscapes/${landscapeId}/external-openapi/download?${qs}`, result.filename).catch((err) => setError(errorText(err)))}>
                <span className="material-symbols-outlined icon-sm">download</span>Download
              </button>
            </>
          }
        />
      )}
    </Modal>
  );
}
