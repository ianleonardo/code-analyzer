import { useEffect, useState } from "react";
import { api, errorText, formatDateTime, post } from "../../api";
import { useAuth } from "../../auth";
import { ConfirmDialog, ErrorNote, PageHeader, Select } from "../../components/ui";
import { can } from "../../permissions";

type Entry = { audit_id: string; ts: string; login_id: string | null; action: string; method: string; path: string; summary: string; status: number; ip_address: string | null; detail: Record<string, unknown> };

export function AuditLogPage() {
  const { user } = useAuth();
  const [entries, setEntries] = useState<Entry[]>([]);
  const [total, setTotal] = useState(0);
  const [actions, setActions] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [action, setAction] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(0);
  const [purging, setPurging] = useState(false);
  const [error, setError] = useState("");
  const size = 100;

  const load = () => {
    const query = new URLSearchParams({ q, action, date_from: from, date_to: to, limit: String(size), offset: String(page * size) });
    api<{ total: number; items: Entry[] }>(`/api/audit?${query}`).then((r) => { setEntries(r.items); setTotal(r.total); }).catch((err) => setError(errorText(err)));
  };
  useEffect(load, [q, action, from, to, page]);
  useEffect(() => { api<string[]>("/api/audit/actions").then(setActions).catch(() => undefined); }, []);

  return (
    <div>
      <PageHeader icon="history" title="Audit Log" subtitle="Every change and sign-in, with who, what and when." right={can(user, "settings", "write") && <button type="button" className="btn btn-danger" disabled={!from || !to} onClick={() => setPurging(true)}>Delete range</button>} />
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap gap-2">
          <input className="field w-64" placeholder="Search summary, user, path…" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} />
          <Select className="field w-56" value={action} onChange={(e) => { setAction(e.target.value); setPage(0); }}>
            <option value="">All actions</option>
            {actions.map((a) => <option key={a} value={a}>{a}</option>)}
          </Select>
          <input className="field w-40" type="date" value={from} onChange={(e) => setFrom(e.target.value)} />
          <input className="field w-40" type="date" value={to} onChange={(e) => setTo(e.target.value)} />
        </div>
        <ErrorNote message={error} />
        <div className="panel">
          <table className="w-full">
            <thead className="table-head"><tr><th>When</th><th>User</th><th>Action</th><th>Summary</th><th>Request</th><th>IP</th></tr></thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.audit_id} className="table-row">
                  <td className="whitespace-nowrap font-mono text-micro text-faint">{formatDateTime(e.ts)}</td>
                  <td className="font-mono text-code">{e.login_id ?? "—"}</td>
                  <td><span className={`chip ${e.action === "login_failed" ? "border-critical/50 text-critical" : "border-line text-muted"}`}>{e.action}</span></td>
                  <td className="max-w-md text-body-md">{e.summary}</td>
                  <td className="max-w-xs truncate font-mono text-micro text-faint">{e.method} {e.path}</td>
                  <td className="font-mono text-micro text-faint">{e.ip_address ?? ""}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="flex items-center justify-end gap-2 border-t border-line px-3 py-1 font-mono text-micro">
            <button type="button" className="btn h-6" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Prev</button>
            <span className="text-muted">{total ? page * size + 1 : 0}–{Math.min(total, (page + 1) * size)} of {total}</span>
            <button type="button" className="btn h-6" disabled={(page + 1) * size >= total} onClick={() => setPage((p) => p + 1)}>Next</button>
          </div>
        </div>
      </div>
      {purging && <ConfirmDialog title="Delete audit entries" message={`Delete every audit entry from ${from} to ${to}?`} onClose={() => setPurging(false)} onConfirm={async () => { await post("/api/audit/purge", { date_from: from, date_to: to }); setPurging(false); load(); }} />}
    </div>
  );
}
