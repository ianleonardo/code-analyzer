import { useMemo, useState } from "react";
import type { Branch } from "../types";

/** Searchable branch selector (master, main, develop and release/* first); any other ref can be typed. */
export function BranchPicker({ branches, value, defaultBranch, onChange }: { branches: Branch[]; value: string; defaultBranch: string; onChange: (value: string) => void }) {
  const [open, setOpen] = useState(false);
  const [q, setQ] = useState("");
  const filtered = useMemo(() => branches.filter((b) => b.name.toLowerCase().includes(q.toLowerCase())).slice(0, 200), [branches, q]);
  return (
    <div className="relative">
      <button type="button" className="field flex items-center justify-between text-left" onClick={() => setOpen((o) => !o)}>
        <span className="flex items-center gap-2 truncate">
          <span className="material-symbols-outlined icon-sm text-primary">call_split</span>
          {value || <span className="text-faint">Choose a branch</span>}
          {value && value === defaultBranch && <span className="chip border-ok/50 text-ok">default</span>}
        </span>
        <span className="material-symbols-outlined icon-sm text-faint">expand_more</span>
      </button>
      {open && (
        <div className="absolute z-50 mt-1 w-full border border-line-strong bg-elevated shadow-[0_4px_16px_-2px_rgba(0,0,0,0.6)]">
          <input autoFocus className="field rounded-none border-0 border-b" placeholder={`Filter ${branches.length} branches or type a ref…`} value={q} onChange={(e) => setQ(e.target.value)} onKeyDown={(e) => {
            if (e.key === "Enter" && q.trim()) {
              onChange(q.trim());
              setOpen(false);
            }
          }} />
          <div className="max-h-64 overflow-auto py-1">
            {filtered.map((b) => (
              <button key={b.name} type="button" className={`flex w-full items-center gap-2 px-2 py-1 text-left font-mono text-code hover:bg-surface ${b.name === value ? "text-primary" : ""}`} onClick={() => { onChange(b.name); setOpen(false); }}>
                <span className="flex-1 truncate">{b.name}</span>
                {b.name === defaultBranch && <span className="chip border-ok/50 text-ok">default</span>}
                {/^(release|hotfix)/i.test(b.name) && <span className="chip border-warning/50 text-warning">release</span>}
                <span className="text-faint">{b.sha?.slice(0, 7)}</span>
              </button>
            ))}
            {q.trim() && !branches.some((b) => b.name === q.trim()) && (
              <button type="button" className="flex w-full items-center gap-2 px-2 py-1 text-left font-mono text-code hover:bg-surface" onClick={() => { onChange(q.trim()); setOpen(false); }}>
                <span className="material-symbols-outlined icon-sm">sell</span> Use ref “{q.trim()}”
              </button>
            )}
            {!filtered.length && !q && <p className="px-2 py-2 text-muted">No branches listed.</p>}
          </div>
        </div>
      )}
    </div>
  );
}
