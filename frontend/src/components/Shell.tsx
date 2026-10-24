import { useCallback, useEffect, useState } from "react";
import { Link, NavLink, Outlet, useNavigate } from "react-router-dom";
import { api } from "../api";
import { useAuth } from "../auth";
import { can } from "../permissions";
import { useTheme } from "../theme";
import type { Dashboard } from "../types";
import { useWorkbench } from "../workbench";
import { SearchPalette } from "./SearchPalette";
import { HealthGauge, ShortSha, SourceBadge, healthColor } from "./ui";

function RailLink({ to, icon, label, end }: { to: string; icon: string; label: string; end?: boolean }) {
  return (
    <NavLink
      to={to}
      end={end}
      title={label}
      className={({ isActive }) =>
        `group relative flex h-11 w-full flex-col items-center justify-center gap-0.5 border-l-2 ${isActive ? "border-primary bg-elevated text-primary" : "border-transparent text-faint hover:text-ink"}`
      }
    >
      <span className="material-symbols-outlined icon-lg">{icon}</span>
      <span className="font-mono text-[8px] uppercase tracking-wider">{label}</span>
    </NavLink>
  );
}

/** Zone 1 (command header), Zone 2 rail, Zone 3 (page outlet) and Zone 4 (telemetry dock). */
export function Shell() {
  const { user, logout } = useAuth();
  const { theme, toggle } = useTheme();
  const { info } = useWorkbench();
  const navigate = useNavigate();
  const [search, setSearch] = useState(false);
  const [menu, setMenu] = useState(false);
  const [dock, setDock] = useState<Dashboard | null>(null);
  const [dockOpen, setDockOpen] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setSearch(true);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  const refresh = useCallback(() => {
    api<Dashboard>("/api/dashboard").then(setDock).catch(() => undefined);
  }, []);
  useEffect(() => {
    refresh();
    const running = (dock?.running?.length ?? 0) > 0;
    const timer = setInterval(refresh, running ? 2500 : 15000);
    return () => clearInterval(timer);
  }, [refresh, dock?.running?.length]);

  if (!user) return null;
  const running = dock?.running ?? [];

  return (
    <div className="flex h-screen flex-col bg-canvas text-ink">
      {/* Zone 1 */}
      <header className="z-40 flex h-14 shrink-0 items-center gap-3 border-b border-line bg-surface px-3">
        <Link to="/" className="flex items-center gap-2 pr-2">
          <span className="material-symbols-outlined icon-lg text-primary">terminal</span>
          <span className="font-mono text-head-sm leading-4 tracking-tight">
            CODE ANALYZER
            <span className="block text-micro text-faint">:: REPO INTELLIGENCE</span>
          </span>
        </Link>
        {info ? (
          <div className="hidden min-w-0 items-center gap-2 border border-line bg-inset px-2 py-1 md:flex">
            <span className={`h-1.5 w-1.5 rounded-full ${info.status === "completed" ? "bg-ok" : info.status === "running" ? "bg-primary pulse-dot" : "bg-warning"}`} />
            <span className="max-w-[220px] truncate font-mono text-code">{info.repoName}</span>
            <span className="font-mono text-code text-faint">::</span>
            <span className="font-mono text-code text-primary">{info.branch || "—"}</span>
            <span className="text-line-strong">|</span>
            <span className="font-mono text-micro text-faint">SHA</span>
            <ShortSha sha={info.sha} />
            {info.merkle && (
              <>
                <span className="text-line-strong">|</span>
                <span className="font-mono text-micro text-faint">MERKLE</span>
                <span className="font-mono text-code text-ok">{info.merkle.slice(0, 6)}…{info.merkle.slice(-4)}</span>
                <span className="chip border-ok/50 bg-ok/10 text-ok">verified</span>
              </>
            )}
          </div>
        ) : (
          <div className="hidden md:block" />
        )}
        <button type="button" className="ml-auto flex h-8 w-72 max-w-[30vw] items-center gap-2 border border-line bg-inset px-2 text-left font-mono text-code text-faint hover:border-primary" onClick={() => setSearch(true)}>
          <span className="material-symbols-outlined icon-sm">search</span>
          <span className="flex-1 truncate">{info ? "sym:Auth* · path · smell" : "Search repositories"}</span>
          <span className="chip border-line text-faint">Ctrl+K</span>
        </button>
        {info && info.health !== null && (
          <div className="hidden items-center gap-2 lg:flex">
            <div className="text-right">
              <div className="font-mono text-micro text-faint">HEALTH</div>
              <div className={`font-mono text-code tnum ${healthColor(info.health)}`}>{info.health?.toFixed(1)} / 100</div>
            </div>
            <HealthGauge score={info.health} size={34} label={false} />
          </div>
        )}
        <div className="hidden items-center gap-1.5 border border-line px-2 py-1 xl:flex" title={dock?.llm_configured ? `LLM: ${dock.llm_provider}` : "No LLM configured"}>
          <span className={`h-1.5 w-1.5 rounded-full ${dock?.llm_configured ? "bg-ok pulse-dot" : "bg-faint"}`} />
          <span className="font-mono text-micro uppercase text-muted">{dock?.llm_configured ? dock.llm_provider : "LLM off"}</span>
        </div>
        <button type="button" className="icon-btn h-8 w-8 border border-line" title={theme === "dark" ? "Switch to Light Laboratory mode" : "Switch to Dark Telemetry mode"} onClick={toggle}>
          <span className="material-symbols-outlined">{theme === "dark" ? "light_mode" : "dark_mode"}</span>
        </button>
        <div className="relative">
          <button type="button" className="flex h-8 items-center gap-2 border border-line px-2 hover:border-primary" onClick={() => setMenu((m) => !m)}>
            <span className="material-symbols-outlined icon-sm text-primary">person</span>
            <span className="hidden text-left sm:block">
              <span className="block max-w-[120px] truncate text-body-sm leading-3">{user.username}</span>
              <span className="block font-mono text-[9px] uppercase leading-3 text-faint">{user.role_name}</span>
            </span>
          </button>
          {menu && (
            <div className="absolute right-0 top-9 z-50 w-56 border border-line-strong bg-elevated py-1 shadow-[0_4px_16px_-2px_rgba(0,0,0,0.6)]" onMouseLeave={() => setMenu(false)}>
              <button type="button" className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-surface" onClick={() => { setMenu(false); navigate("/git-connections"); }}>
                <span className="material-symbols-outlined icon-sm">key</span> Git connections
              </button>
              <button type="button" className="flex w-full items-center gap-2 px-3 py-1.5 text-left hover:bg-surface" onClick={() => { setMenu(false); navigate("/account"); }}>
                <span className="material-symbols-outlined icon-sm">password</span> Change password
              </button>
              <div className="my-1 border-t border-line" />
              <button type="button" className="flex w-full items-center gap-2 px-3 py-1.5 text-left text-critical hover:bg-surface" onClick={() => { logout(); navigate("/login"); }}>
                <span className="material-symbols-outlined icon-sm">logout</span> Sign out
              </button>
            </div>
          )}
        </div>
      </header>

      <div className="flex min-h-0 flex-1">
        {/* Zone 2: navigation rail */}
        <nav className="flex w-[60px] shrink-0 flex-col border-r border-line bg-surface py-1" aria-label="Primary">
          <RailLink to="/" icon="monitoring" label="Pulse" end />
          {can(user, "repositories") && <RailLink to="/repositories" icon="folder_data" label="Repos" />}
          {can(user, "repositories") && <RailLink to="/analyses" icon="query_stats" label="Runs" />}
          {can(user, "insights") && can(user, "repositories") && <RailLink to="/compare" icon="compare_arrows" label="Compare" />}
          {can(user, "repositories") && <RailLink to="/impact" icon="difference" label="Impact" />}
          <div className="flex-1" />
          {can(user, "insights") && can(user, "repositories") && <RailLink to="/chat" icon="forum" label="Chat" />}
          <RailLink to="/git-connections" icon="key" label="Git" />
          {can(user, "settings") && <RailLink to="/settings" icon="tune" label="Admin" />}
        </nav>
        {/* Zone 3 */}
        <main className="min-w-0 flex-1 overflow-auto">
          <Outlet />
        </main>
      </div>

      {/* Zone 4: telemetry dock */}
      <footer className={`shrink-0 border-t border-line bg-surface transition-[height] ${dockOpen ? "h-[180px]" : "h-9"}`}>
        <div className="flex h-9 items-center gap-3 px-3 font-mono text-micro">
          <span className={`h-1.5 w-1.5 rounded-full ${running.length ? "bg-primary pulse-dot" : "bg-ok"}`} />
          {running.length ? (
            <>
              <span className="text-ink">ANALYSIS ENGINE: {running.length} ACTIVE</span>
              <span className="truncate text-primary">
                {running[0].repo_name}@{running[0].branch || "-"} · {running[0].stage} · {running[0].message}
              </span>
              <div className="hidden h-1.5 w-48 bg-elevated md:block">
                <div className="h-full bg-primary stripes" style={{ width: `${running[0].progress}%` }} />
              </div>
              <span className="text-primary tnum">{Math.round(running[0].progress)}%</span>
            </>
          ) : (
            <span className="text-muted">ANALYSIS ENGINE: IDLE · DETERMINISTIC GATING READY</span>
          )}
          <span className="ml-auto hidden items-center gap-3 text-faint lg:flex">
            <span>LLM: <span className={dock?.llm_configured ? "text-ok" : "text-faint"}>{dock?.llm_configured ? dock.llm_provider : "not configured"}</span></span>
            <span>EMBEDDINGS: <span className={dock?.embedding_configured ? "text-ok" : "text-faint"}>{dock?.embedding_configured ? "on" : "BM25 only"}</span></span>
            {info && <SourceBadge source={info.source} />}
          </span>
          <button type="button" className="icon-btn" title={dockOpen ? "Collapse console" : "Expand console"} onClick={() => setDockOpen((o) => !o)}>
            <span className="material-symbols-outlined">{dockOpen ? "expand_more" : "expand_less"}</span>
          </button>
        </div>
        {dockOpen && (
          <div className="h-[143px] overflow-auto border-t border-line bg-inset px-3 py-2 font-mono text-micro">
            {running.length === 0 && <p className="text-faint">No analysis is running. Recent completed work is on the Pulse dashboard.</p>}
            {running.map((job) => (
              <Link key={job.analysis_id} to={`/analyses/${job.analysis_id}`} className="flex items-center gap-3 py-0.5 hover:text-primary">
                <span className="w-56 truncate text-ink">{job.repo_name}@{job.branch || "-"}</span>
                <span className="w-44 truncate text-muted">{job.stage}</span>
                <span className="w-10 text-right text-primary tnum">{Math.round(job.progress)}%</span>
                <span className="flex-1 truncate text-faint">{job.message}</span>
              </Link>
            ))}
          </div>
        )}
      </footer>
      <SearchPalette open={search} onClose={() => setSearch(false)} />
    </div>
  );
}
