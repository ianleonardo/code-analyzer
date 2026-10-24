import { useCallback, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, compact, del, formatDateTime } from "../api";
import { useAuth } from "../auth";
import { ConfirmDialog, HealthGauge, PageHeader, Select, ShortSha, SourceBadge, StatusBadge } from "../components/ui";
import { can } from "../permissions";
import type { AnalysisBrief } from "../types";

export function AnalysesPage() {
  const { user } = useAuth();
  const canWrite = can(user, "repositories", "write");
  const [rows, setRows] = useState<AnalysisBrief[]>([]);
  const [status, setStatus] = useState("");
  const [deleting, setDeleting] = useState<AnalysisBrief | null>(null);

  const load = useCallback(() => {
    api<AnalysisBrief[]>(`/api/analyses?limit=300${status ? `&status=${status}` : ""}`).then(setRows).catch(() => setRows([]));
  }, [status]);

  useEffect(() => {
    load();
    const timer = setInterval(load, 4000);
    return () => clearInterval(timer);
  }, [load]);

  return (
    <div>
      <PageHeader
        icon="query_stats"
        title="Analysis runs"
        subtitle="Every run across all repositories and branches."
        right={
          <Select className="field w-44" value={status} onChange={(e) => setStatus(e.target.value)}>
            <option value="">All statuses</option>
            {["running", "queued", "completed", "failed", "cancelled"].map((s) => <option key={s} value={s}>{s}</option>)}
          </Select>
        }
      />
      <div className="p-4">
        <div className="panel">
          <table className="w-full">
            <thead className="table-head"><tr><th>Repository</th><th>Branch · commit</th><th>Status</th><th>Stage</th><th>Health</th><th className="text-right">Files</th><th className="text-right">LOC</th><th>Started</th>{canWrite && <th className="w-16 text-right">Actions</th>}</tr></thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.analysis_id} className="table-row">
                  <td><Link to={`/analyses/${row.analysis_id}`} className="font-mono text-code hover:text-primary">{row.repo_name}</Link><div><SourceBadge source={row.source_type ?? "git"} /></div></td>
                  <td className="font-mono text-code"><span className="text-primary">{row.branch || "—"}</span> <ShortSha sha={row.commit_sha} /></td>
                  <td><StatusBadge status={row.status} progress={row.progress} /></td>
                  <td className="max-w-[320px] truncate font-mono text-micro text-muted">{row.stage} · {row.message}</td>
                  <td><HealthGauge score={row.health_score} size={30} /></td>
                  <td className="text-right font-mono tnum">{compact(row.file_count)}</td>
                  <td className="text-right font-mono tnum">{compact(row.loc_total)}</td>
                  <td className="font-mono text-micro text-faint">{formatDateTime(row.created_at)}</td>
                  {canWrite && (
                    <td className="text-right">
                      <button
                        type="button"
                        className="icon-btn hover:!text-critical"
                        title="Delete analysis run"
                        onClick={() => setDeleting(row)}
                      >
                        <span className="material-symbols-outlined icon-sm">delete</span>
                      </button>
                    </td>
                  )}
                </tr>
              ))}
            </tbody>
          </table>
          {!rows.length && <p className="p-6 text-center text-muted">No runs.</p>}
        </div>
      </div>
      {deleting && (
        <ConfirmDialog
          title="Delete analysis"
          message={<>Delete analysis run for <strong>{deleting.repo_name}</strong> ({deleting.branch || "archive"}) and its associated chats and comparisons?</>}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await del(`/api/analyses/${deleting.analysis_id}`);
            setDeleting(null);
            load();
          }}
        />
      )}
    </div>
  );
}
