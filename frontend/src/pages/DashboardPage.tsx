import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, compact, timeAgo } from "../api";
import { useAuth } from "../auth";
import { AddRepositoryDialog } from "../components/AddRepository";
import { HealthGauge, PageHeader, ShortSha, SourceBadge, StatusBadge, healthColor } from "../components/ui";
import { can } from "../permissions";
import type { Dashboard } from "../types";

function Kpi({ label, value, hint, accent = "text-ink", to }: { label: string; value: string; hint?: string; accent?: string; to?: string }) {
  const content = (
    <div className={`card ${to ? "hover:border-primary/50 transition-colors" : ""}`}>
      <div className="label-micro">{label}</div>
      <div className={`kpi mt-1 ${accent}`}>{value}</div>
      {hint && <div className="font-mono text-micro text-faint">{hint}</div>}
    </div>
  );
  if (to) {
    return <Link to={to} className="block">{content}</Link>;
  }
  return content;
}

export function DashboardPage() {
  const { user } = useAuth();
  const [data, setData] = useState<Dashboard | null>(null);
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    const load = () => api<Dashboard>("/api/dashboard").then(setData).catch(() => undefined);
    load();
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  }, []);

  const canWrite = can(user, "repositories", "write");
  const analyses = data?.analyses ?? {};
  return (
    <div>
      <PageHeader
        icon="monitoring"
        title="Telemetry Pulse"
        subtitle="Repositories, analysis runs and codebase health across the workspace."
        right={
          <>
            <Link className="btn" to="/analyses">
              <span className="material-symbols-outlined icon-sm">query_stats</span>All runs
            </Link>
            {can(user, "insights") && <Link className="btn" to="/compare"><span className="material-symbols-outlined icon-sm">compare_arrows</span>Compare</Link>}
            {canWrite && (
              <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>
                <span className="material-symbols-outlined icon-sm">add</span>Add repository
              </button>
            )}
          </>
        }
      />
      <div className="space-y-4 p-4">
        <div className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
          <Kpi label="Repositories" value={String(data?.repositories ?? "—")} />
          <Kpi
            label="Completed runs"
            value={String(analyses.completed ?? 0)}
            hint={`${analyses.failed ?? 0} failed · ${analyses.cancelled ?? 0} cancelled`}
            to="/analyses"
          />
          <Kpi
            label="Active runs"
            value={String((analyses.running ?? 0) + (analyses.queued ?? 0))}
            accent={(analyses.running ?? 0) ? "text-primary" : "text-ink"}
            to="/analyses"
          />
          <Kpi label="Lines analysed" value={compact(data?.total_loc ?? 0)} />
          <Kpi label="Avg health" value={data?.avg_health !== null && data?.avg_health !== undefined ? data.avg_health.toFixed(1) : "—"} accent={healthColor(data?.avg_health)} hint="/ 100" />
          <Kpi label="Comparisons · chats" value={`${data?.comparisons ?? 0} · ${data?.chats ?? 0}`} />
        </div>

        <section className="panel">
          <div className="panel-head">
            <span>Recent analysis runs</span>
            <Link to="/analyses" className="btn h-6 px-2 text-micro">
              All runs →
            </Link>
          </div>
          <table className="w-full">
            <thead className="table-head">
              <tr><th>Repository</th><th>Branch · commit</th><th>Status</th><th>Health</th><th>Style</th><th className="text-right">Files</th><th className="text-right">LOC</th><th>When</th></tr>
            </thead>
            <tbody>
              {(data?.recent ?? []).map((row) => (
                <tr key={row.analysis_id} className="table-row">
                  <td>
                    <Link to={`/analyses/${row.analysis_id}`} className="font-mono text-code hover:text-primary">{row.repo_name}</Link>
                    <div><SourceBadge source={row.source_type ?? "git"} /></div>
                  </td>
                  <td className="font-mono text-code"><span className="text-primary">{row.branch || "—"}</span> <ShortSha sha={row.commit_sha} /></td>
                  <td><StatusBadge status={row.status} progress={row.progress} /></td>
                  <td><HealthGauge score={row.health_score} size={30} /></td>
                  <td className="text-body-sm text-muted">{row.architecture_style || "—"}</td>
                  <td className="text-right font-mono tnum">{compact(row.file_count)}</td>
                  <td className="text-right font-mono tnum">{compact(row.loc_total)}</td>
                  <td className="font-mono text-micro text-faint">{timeAgo(row.finished_at ?? row.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!data?.recent?.length && (
            <div className="p-8 text-center text-muted">
              No analyses yet.{" "}
              {canWrite && <button type="button" className="link" onClick={() => setAdding(true)}>Add a repository</button>}
            </div>
          )}
        </section>
      </div>
      {adding && <AddRepositoryDialog onClose={() => setAdding(false)} />}
    </div>
  );
}
