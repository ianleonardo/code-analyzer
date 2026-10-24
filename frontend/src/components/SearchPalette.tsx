import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api } from "../api";
import type { Repository } from "../types";
import { useWorkbench } from "../workbench";
import { LayerPill, SeverityBadge } from "./ui";

type Results = {
  files: { path: string; layer: string; role: string; language: string }[];
  symbols: { path: string; qualified_name: string; kind: string; line_start: number }[];
  concerns: { severity: string; category: string; title: string; path: string }[];
};

/** Ctrl+K: files, symbols and concerns of the open analysis; repositories elsewhere. */
export function SearchPalette({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { info } = useWorkbench();
  const navigate = useNavigate();
  const [q, setQ] = useState("");
  const [results, setResults] = useState<Results | null>(null);
  const [repos, setRepos] = useState<Repository[]>([]);
  const input = useRef<HTMLInputElement>(null);
  const scoped = info && info.status === "completed";

  useEffect(() => {
    if (!open) return;
    setQ("");
    setResults(null);
    setTimeout(() => input.current?.focus(), 10);
    if (!scoped) api<Repository[]>("/api/repositories").then(setRepos).catch(() => setRepos([]));
  }, [open, scoped]);

  useEffect(() => {
    if (!open || !scoped || q.trim().length < 2) {
      setResults(null);
      return;
    }
    const timer = setTimeout(() => {
      api<Results>(`/api/analyses/${info!.analysisId}/search?q=${encodeURIComponent(q)}`).then(setResults).catch(() => setResults(null));
    }, 180);
    return () => clearTimeout(timer);
  }, [q, open, scoped, info]);

  if (!open) return null;
  const go = (url: string) => {
    onClose();
    navigate(url);
  };
  const base = info ? `/analyses/${info.analysisId}` : "";
  const filteredRepos = repos.filter((r) => r.name.toLowerCase().includes(q.toLowerCase())).slice(0, 12);

  return (
    <div className="fixed inset-0 z-50 flex justify-center bg-canvas/70 pt-24 backdrop-blur-[4px]" onMouseDown={(e) => e.target === e.currentTarget && onClose()}>
      <div className="h-fit max-h-[70vh] w-full max-w-2xl overflow-hidden border border-line-strong bg-elevated shadow-[0_4px_16px_-2px_rgba(0,0,0,0.6)]">
        <div className="flex items-center gap-2 border-b border-line px-3">
          <span className="material-symbols-outlined text-primary">search</span>
          <input
            ref={input}
            className="h-11 flex-1 bg-transparent font-mono text-body-lg outline-none"
            placeholder={scoped ? `Search files, symbols, concerns in ${info!.repoName}…` : "Search repositories…"}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Escape" && onClose()}
          />
          <span className="chip border-line text-faint">ESC</span>
        </div>
        <div className="max-h-[60vh] overflow-auto p-1">
          {scoped && results && (
            <>
              {results.files.length > 0 && <p className="label-micro px-2 pt-2">Files</p>}
              {results.files.map((f) => (
                <button key={f.path} type="button" className="flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-surface" onClick={() => go(`${base}?tab=inspector&file=${encodeURIComponent(f.path)}`)}>
                  <span className="material-symbols-outlined icon-sm text-faint">description</span>
                  <span className="flex-1 truncate font-mono text-code">{f.path}</span>
                  <LayerPill layer={f.layer} />
                </button>
              ))}
              {results.symbols.length > 0 && <p className="label-micro px-2 pt-2">Symbols</p>}
              {results.symbols.map((s) => (
                <button key={`${s.path}:${s.qualified_name}:${s.line_start}`} type="button" className="flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-surface" onClick={() => go(`${base}?tab=inspector&file=${encodeURIComponent(s.path)}&line=${s.line_start}`)}>
                  <span className="chip border-line text-muted">{s.kind}</span>
                  <span className="font-mono text-code text-primary">{s.qualified_name}</span>
                  <span className="flex-1 truncate text-right font-mono text-micro text-faint">{s.path}:{s.line_start}</span>
                </button>
              ))}
              {results.concerns.length > 0 && <p className="label-micro px-2 pt-2">Concerns</p>}
              {results.concerns.map((c, i) => (
                <button key={i} type="button" className="flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-surface" onClick={() => go(`${base}?tab=quality`)}>
                  <SeverityBadge severity={c.severity} />
                  <span className="flex-1 truncate">{c.title}</span>
                </button>
              ))}
              {!results.files.length && !results.symbols.length && !results.concerns.length && <p className="p-4 text-muted">No matches.</p>}
            </>
          )}
          {scoped && !results && <p className="p-4 text-muted text-body-md">Type at least two characters. Search covers file paths, symbol names (Class.method) and concern titles.</p>}
          {!scoped &&
            filteredRepos.map((r) => (
              <button key={r.repo_id} type="button" className="flex w-full items-center gap-2 px-2 py-1.5 text-left hover:bg-surface" onClick={() => go(r.latest_analysis ? `/analyses/${r.latest_analysis.analysis_id}` : `/repositories/${r.repo_id}`)}>
                <span className="material-symbols-outlined icon-sm text-faint">folder_data</span>
                <span className="flex-1 truncate font-mono text-code">{r.name}</span>
                <span className="font-mono text-micro text-faint">{r.provider_label}</span>
              </button>
            ))}
        </div>
      </div>
    </div>
  );
}
