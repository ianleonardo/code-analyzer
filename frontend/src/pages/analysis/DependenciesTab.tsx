import { useEffect, useMemo, useState } from "react";
import { api } from "../../api";
import { Select } from "../../components/ui";
import type { Analysis } from "../../types";

type Dep = { container: string; ecosystem: string; name: string; version: string; scope: string; manifest: string };

export function DependenciesTab({ analysis }: { analysis: Analysis }) {
  const [rows, setRows] = useState<Dep[]>([]);
  const [q, setQ] = useState("");
  const [scope, setScope] = useState("");
  useEffect(() => {
    api<Dep[]>(`/api/analyses/${analysis.analysis_id}/dependencies`).then(setRows);
  }, [analysis.analysis_id]);
  const known = useMemo(() => new Map((analysis.macro.frameworks ?? []).map((fw) => [fw.evidence.split("→ ").pop()?.trim().toLowerCase(), fw])), [analysis]);
  const shown = rows.filter((r) => r.name.toLowerCase().includes(q.toLowerCase()) && (!scope || r.scope === scope));
  return (
    <div className="flex h-full flex-col">
      <div className="flex items-center gap-2 border-b border-line bg-surface px-3 py-1.5">
        <input className="field h-7 w-64" placeholder="Filter packages…" value={q} onChange={(e) => setQ(e.target.value)} />
        <Select className="field h-7 w-36" value={scope} onChange={(e) => setScope(e.target.value)}>
          <option value="">All scopes</option>
          {[...new Set(rows.map((r) => r.scope))].map((s) => <option key={s} value={s}>{s}</option>)}
        </Select>
        <span className="ml-auto font-mono text-micro text-faint">{shown.length} declared dependencies · {analysis.macro.external_packages?.length ?? 0} imported external packages</span>
      </div>
      <div className="min-h-0 flex-1 overflow-auto">
        <table className="w-full">
          <thead className="table-head"><tr><th>Package</th><th>Version</th><th>Scope</th><th>Container</th><th>Ecosystem</th><th>Detected as</th></tr></thead>
          <tbody>
            {shown.map((r, i) => {
              const fw = known.get(r.name.toLowerCase());
              return (
                <tr key={i} className="table-row">
                  <td className="font-mono text-code">{r.name}</td>
                  <td className="font-mono text-code text-muted">{r.version || "—"}</td>
                  <td><span className={`chip ${r.scope === "runtime" ? "border-primary/40 text-primary" : "border-line text-faint"}`}>{r.scope}</span></td>
                  <td className="font-mono text-code">{r.container}</td>
                  <td className="font-mono text-micro text-muted">{r.ecosystem}</td>
                  <td>{fw && <span className="chip border-ok/40 text-ok normal-case tracking-normal">{fw.name} · {fw.category}</span>}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
