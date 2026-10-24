import { useEffect, useState } from "react";
import { api, errorText, formatNumber } from "../../api";
import { ErrorNote, PageHeader } from "../../components/ui";

type Info = { engine: string; location: string; schema_version: number; tables: Record<string, number> };

export function DatabasePage() {
  const [info, setInfo] = useState<Info | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api<Info>("/api/database").then(setInfo).catch((err) => setError(errorText(err))); }, []);
  return (
    <div>
      <PageHeader icon="database" title="Database" subtitle="One schema, two engines: DuckDB (single file) or PostgreSQL. Every analysis, chat and comparison is stored here for offline use." />
      <div className="grid max-w-6xl gap-4 p-4 lg:grid-cols-[1fr_1fr]">
        <ErrorNote message={error} />
        {info && (
          <>
            <section className="panel p-3 space-y-2 font-mono text-code">
              <div className="flex justify-between"><span className="text-faint">Engine</span><span className="uppercase text-primary">{info.engine}</span></div>
              <div className="flex justify-between gap-4"><span className="text-faint">Location</span><span className="truncate">{info.location}</span></div>
              <div className="flex justify-between"><span className="text-faint">Schema version</span><span>v{info.schema_version}</span></div>
              <div className="border-t border-line pt-2 text-body-md font-sans text-muted">
                <p className="font-medium text-ink">Switching engines</p>
                <p className="mt-1">Set <code className="font-mono text-primary">DB_ENGINE=postgres</code> and <code className="font-mono text-primary">DATABASE_URL=postgresql://user:pass@host:5432/codeanalyzer</code> in <code className="font-mono">.env</code>, or <code className="font-mono text-primary">DB_ENGINE=duckdb</code> with <code className="font-mono">DUCKDB_PATH</code>. The same tables are created on start-up.</p>
                <p className="mt-1">Copy existing data across with <code className="font-mono text-primary">cd backend && python -m app.tools.migrate_db --from duckdb --to postgres</code> (see README).</p>
              </div>
            </section>
            <section className="panel">
              <div className="panel-head"><span>Tables</span><span>{Object.keys(info.tables).length}</span></div>
              <table className="w-full"><tbody>
                {Object.entries(info.tables).map(([name, count]) => (
                  <tr key={name} className="table-row"><td className="font-mono text-code">{name}</td><td className="text-right font-mono tnum">{formatNumber(count)}</td></tr>
                ))}
              </tbody></table>
            </section>
          </>
        )}
      </div>
    </div>
  );
}
