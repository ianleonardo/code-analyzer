import { useEffect, useState } from "react";
import { errorText, post } from "../api";
import { ErrorNote, Modal } from "./ui";

/** `value` after it has stopped changing for `delay` ms (for search boxes that query the server). */
export function useDebounced<T>(value: T, delay = 300): T {
  const [debounced, setDebounced] = useState(value);
  useEffect(() => {
    const timer = setTimeout(() => setDebounced(value), delay);
    return () => clearTimeout(timer);
  }, [value, delay]);
  return debounced;
}

export type Page<T> = { items: T[]; total: number; page: number; page_size: number };

export type PurgeResult = { matched: number; deleted: number; skipped_active: number; dry_run: boolean };

/** Local calendar date (YYYY-MM-DD) → UTC ISO instant at local midnight. `endOfDay` returns the next midnight (exclusive bound). */
export function dayToIso(day: string, endOfDay = false): string {
  if (!day) return "";
  const date = new Date(`${day}T00:00:00`);
  if (endOfDay) date.setDate(date.getDate() + 1);
  return date.toISOString();
}

export function Pagination({ page, pageSize, total, onPage }: { page: number; pageSize: number; total: number; onPage: (page: number) => void }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (total <= pageSize) return <div className="px-3 py-2 font-mono text-micro text-faint">{total} item{total === 1 ? "" : "s"}</div>;
  const first = (page - 1) * pageSize + 1;
  const last = Math.min(total, page * pageSize);
  return (
    <div className="flex items-center justify-between gap-2 border-t border-line px-3 py-2">
      <span className="font-mono text-micro text-faint">{first}–{last} of {total}</span>
      <div className="flex items-center gap-1">
        <button type="button" className="icon-btn" title="First page" disabled={page <= 1} onClick={() => onPage(1)}><span className="material-symbols-outlined icon-sm">first_page</span></button>
        <button type="button" className="icon-btn" title="Previous page" disabled={page <= 1} onClick={() => onPage(page - 1)}><span className="material-symbols-outlined icon-sm">chevron_left</span></button>
        <span className="px-2 font-mono text-micro">Page {page} / {pages}</span>
        <button type="button" className="icon-btn" title="Next page" disabled={page >= pages} onClick={() => onPage(page + 1)}><span className="material-symbols-outlined icon-sm">chevron_right</span></button>
        <button type="button" className="icon-btn" title="Last page" disabled={page >= pages} onClick={() => onPage(pages)}><span className="material-symbols-outlined icon-sm">last_page</span></button>
      </div>
    </div>
  );
}

/** Date-range filter shared by the list and the purge dialog. Values are local calendar days (YYYY-MM-DD). */
export function DateRangeFilter({ from, to, onChange }: { from: string; to: string; onChange: (from: string, to: string) => void }) {
  return (
    <div className="flex flex-wrap items-end gap-2">
      <label className="block">
        <span className="label-micro">From</span>
        <input type="date" className="field mt-1" value={from} max={to || undefined} onChange={(e) => onChange(e.target.value, to)} />
      </label>
      <label className="block">
        <span className="label-micro">To (inclusive)</span>
        <input type="date" className="field mt-1" value={to} min={from || undefined} onChange={(e) => onChange(from, e.target.value)} />
      </label>
      {(from || to) && <button type="button" className="btn h-8" onClick={() => onChange("", "")}>Clear</button>}
    </div>
  );
}

/**
 * Purge by date range: preview (dry run) shows how many items match, then a second click deletes them.
 * `extra` is merged into the request body (e.g. repo_id to limit a purge to one repository).
 */
export function PurgeDialog({ title, endpoint, noun, extra = {}, initialFrom = "", initialTo = "", note, onClose, onDone }: {
  title: string;
  endpoint: string;
  noun: string;
  extra?: Record<string, unknown>;
  initialFrom?: string;
  initialTo?: string;
  note?: string;
  onClose: () => void;
  onDone: (result: PurgeResult) => void;
}) {
  const [from, setFrom] = useState(initialFrom);
  const [to, setTo] = useState(initialTo);
  const [preview, setPreview] = useState<PurgeResult | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  const body = (dryRun: boolean) => ({ ...extra, date_from: dayToIso(from), date_to: dayToIso(to, true), dry_run: dryRun });

  async function run(dryRun: boolean) {
    setBusy(true);
    setError("");
    try {
      const result = await post<PurgeResult>(endpoint, body(dryRun));
      if (dryRun) setPreview(result);
      else onDone(result);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={title}
      onClose={onClose}
      width="max-w-md"
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          {preview && preview.matched > 0 ? (
            <button type="button" className="btn btn-danger" disabled={busy} onClick={() => void run(false)}>
              {busy ? "Deleting…" : `Delete ${preview.matched} ${noun}`}
            </button>
          ) : (
            <button type="button" className="btn btn-primary" disabled={busy || (!from && !to)} onClick={() => void run(true)}>
              {busy ? "Counting…" : "Preview"}
            </button>
          )}
        </>
      }
    >
      <div className="space-y-3">
        <p className="text-body-sm text-muted">Permanently delete {noun} created in this date range. Leave one side empty for an open-ended range. This cannot be undone.</p>
        <DateRangeFilter from={from} to={to} onChange={(f, t) => { setFrom(f); setTo(t); setPreview(null); }} />
        {note && <p className="text-body-sm text-faint">{note}</p>}
        {preview && (
          <p className={`text-body-md ${preview.matched ? "text-warning" : "text-muted"}`}>
            {preview.matched ? `${preview.matched} ${noun} will be deleted.` : `No ${noun} match this range.`}
            {preview.skipped_active > 0 && ` ${preview.skipped_active} queued/running item(s) will be kept.`}
          </p>
        )}
        <ErrorNote message={error} />
      </div>
    </Modal>
  );
}
