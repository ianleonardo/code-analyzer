import { useEffect, useState } from "react";
import { api, errorText, formatDateTime } from "../../api";
import { useAuth } from "../../auth";
import { ErrorNote, PageHeader, Select } from "../../components/ui";
import { DateRangeFilter, Pagination, PurgeDialog } from "../../components/ListTools";
import { can } from "../../permissions";

type Entry = {
  audit_id: string;
  ts: string;
  login_id: string | null;
  action: string;
  method: string;
  path: string;
  summary: string;
  status: number;
  ip_address: string | null;
  detail: Record<string, unknown>;
};

export function AuditLogPage() {
  const { user } = useAuth();
  const [entries, setEntries] = useState<Entry[]>([]);
  const [total, setTotal] = useState(0);
  const [actions, setActions] = useState<string[]>([]);
  const [q, setQ] = useState("");
  const [action, setAction] = useState("");
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [page, setPage] = useState(1);
  const [purging, setPurging] = useState(false);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const size = 100;

  const load = () => {
    const query = new URLSearchParams({
      q,
      action,
      date_from: from,
      date_to: to,
      limit: String(size),
      offset: String((page - 1) * size),
    });
    api<{ total: number; items: Entry[] }>(`/api/audit?${query}`)
      .then((r) => {
        setEntries(r.items);
        setTotal(r.total);
      })
      .catch((err) => setError(errorText(err)));
  };

  useEffect(load, [q, action, from, to, page]);
  useEffect(() => {
    api<string[]>("/api/audit/actions").then(setActions).catch(() => undefined);
  }, []);

  return (
    <div>
      <PageHeader
        icon="history"
        title="Audit Log"
        subtitle="Every change and sign-in, with who, what and when."
        right={
          can(user, "settings", "write") && (
            <button
              type="button"
              className="btn btn-danger"
              onClick={() => setPurging(true)}
            >
              <span className="material-symbols-outlined icon-sm">delete_sweep</span>
              Delete range
            </button>
          )
        }
      />
      <div className="space-y-3 p-4">
        <div className="flex flex-wrap items-end gap-3">
          <label className="block">
            <span className="label-micro">Search</span>
            <input
              className="field mt-1 w-64"
              placeholder="Search summary, user, path…"
              value={q}
              onChange={(e) => {
                setQ(e.target.value);
                setPage(1);
              }}
            />
          </label>
          <label className="block">
            <span className="label-micro">Action</span>
            <Select
              className="field mt-1 w-56"
              value={action}
              onChange={(e) => {
                setAction(e.target.value);
                setPage(1);
              }}
            >
              <option value="">All actions</option>
              {actions.map((a) => (
                <option key={a} value={a}>
                  {a}
                </option>
              ))}
            </Select>
          </label>
          <DateRangeFilter
            from={from}
            to={to}
            onChange={(f, t) => {
              setFrom(f);
              setTo(t);
              setPage(1);
            }}
          />
        </div>
        {notice && (
          <div className="rounded-md border border-line-strong bg-surface p-2.5 text-body-sm text-ink flex items-center justify-between">
            <span>{notice}</span>
            <button type="button" className="text-muted hover:text-ink" onClick={() => setNotice("")}>
              <span className="material-symbols-outlined icon-sm">close</span>
            </button>
          </div>
        )}
        <ErrorNote message={error} />
        <div className="panel">
          <table className="w-full">
            <thead className="table-head">
              <tr>
                <th>When</th>
                <th>User</th>
                <th>Action</th>
                <th>Summary</th>
                <th>Request</th>
                <th>IP</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => (
                <tr key={e.audit_id} className="table-row">
                  <td className="whitespace-nowrap font-mono text-micro text-faint">{formatDateTime(e.ts)}</td>
                  <td className="font-mono text-code">{e.login_id ?? "—"}</td>
                  <td>
                    <span
                      className={`chip ${
                        e.action === "login_failed" ? "border-critical/50 text-critical" : "border-line text-muted"
                      }`}
                    >
                      {e.action}
                    </span>
                  </td>
                  <td className="max-w-md text-body-md">{e.summary}</td>
                  <td className="max-w-xs truncate font-mono text-micro text-faint">
                    {e.method} {e.path}
                  </td>
                  <td className="font-mono text-micro text-faint">{e.ip_address ?? ""}</td>
                </tr>
              ))}
              {!entries.length && (
                <tr>
                  <td colSpan={6} className="p-8 text-center text-muted">
                    No audit log entries found.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
          <Pagination page={page} pageSize={size} total={total} onPage={setPage} />
        </div>
      </div>
      {purging && (
        <PurgeDialog
          title="Delete audit entries"
          endpoint="/api/audit/purge"
          noun="audit entry / entries"
          initialFrom={from}
          initialTo={to}
          onClose={() => setPurging(false)}
          onDone={(result) => {
            setPurging(false);
            setNotice(`Successfully deleted ${result.deleted} audit log entries.`);
            load();
          }}
        />
      )}
    </div>
  );
}
