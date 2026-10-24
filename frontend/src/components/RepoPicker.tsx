import { useEffect, useMemo, useRef, useState } from "react";
import { timeAgo } from "../api";
import type { Repository } from "../types";
import { Select, SourceBadge } from "./ui";

const SOURCE_ORDER = ["github", "bitbucket", "bitbucket_server", "git", "local", "zip"];
const PIN_KEY = "ca.repoPicker.pinned";
const RECENT_KEY = "ca.repoPicker.recent";
const RECENT_MAX = 8;
const ROW_LIMIT = 150;

/** Project / owner a repository belongs to (Bitbucket project key, GitHub owner, or the first part of "group/name"). */
export function repoProject(repo: Repository): string {
  if (repo.owner?.trim()) return repo.owner.trim();
  if (repo.name.includes("/")) {
    const prefix = repo.name.split("/")[0]?.trim();
    if (prefix) return prefix;
  }
  if (repo.source_type === "local") return "Local workspace";
  if (repo.source_type === "zip") return "Uploaded archives";
  return repo.host || "Default project";
}

/** Repository name without its project prefix. */
export function repoShortName(repo: Repository): string {
  if (repo.slug?.trim()) return repo.slug.trim();
  if (repo.name.includes("/")) {
    const rest = repo.name.split("/").slice(1).join("/").trim();
    if (rest) return rest;
  }
  return repo.name;
}

function readIds(key: string): string[] {
  try {
    const value = JSON.parse(localStorage.getItem(key) || "[]");
    return Array.isArray(value) ? value.filter((v): v is string => typeof v === "string") : [];
  } catch {
    return [];
  }
}

function writeIds(key: string, ids: string[]) {
  try {
    localStorage.setItem(key, JSON.stringify(ids));
  } catch {
    /* storage unavailable: pins and recents just are not remembered */
  }
}

function sourceRank(source: string) {
  const index = SOURCE_ORDER.indexOf(source);
  return index === -1 ? 99 : index;
}

function Highlight({ text, query }: { text: string; query: string }) {
  if (!query) return <>{text}</>;
  const at = text.toLowerCase().indexOf(query);
  if (at < 0) return <>{text}</>;
  return (
    <>
      {text.slice(0, at)}
      <mark className="rounded-sm bg-primary/25 text-ink">{text.slice(at, at + query.length)}</mark>
      {text.slice(at + query.length)}
    </>
  );
}

type Row = { kind: "header"; label: string; count?: number } | { kind: "repo"; repo: Repository; key: string } | { kind: "all" };

/**
 * Repository chooser for long lists: host chips and a project filter narrow the list, then a type-to-search
 * combobox (name, project, URL, default branch) with keyboard navigation. Pinned and recently used repositories are
 * remembered in this browser and shown first.
 */
export function RepoPicker({ repos, value, onChange, allowAll = true }: { repos: Repository[]; value: string; onChange: (repoId: string) => void; allowAll?: boolean }) {
  const [source, setSource] = useState("all");
  const [project, setProject] = useState("");
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [pinned, setPinned] = useState<string[]>(() => readIds(PIN_KEY));
  const [recent, setRecent] = useState<string[]>(() => readIds(RECENT_KEY));
  const boxRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  const selected = repos.find((r) => r.repo_id === value) ?? null;

  const sources = useMemo(() => {
    const counts = new Map<string, { label: string; count: number }>();
    for (const repo of repos) {
      const entry = counts.get(repo.source_type) ?? { label: repo.provider_label || repo.source_type, count: 0 };
      entry.count += 1;
      counts.set(repo.source_type, entry);
    }
    return [...counts.entries()].sort((a, b) => sourceRank(a[0]) - sourceRank(b[0]));
  }, [repos]);

  const inSource = useMemo(() => repos.filter((r) => source === "all" || r.source_type === source), [repos, source]);

  const projects = useMemo(() => {
    const counts = new Map<string, number>();
    for (const repo of inSource) counts.set(repoProject(repo), (counts.get(repoProject(repo)) ?? 0) + 1);
    return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
  }, [inSource]);

  // A project that no longer exists under the chosen host is dropped.
  useEffect(() => {
    if (project && !projects.some(([name]) => name === project)) setProject("");
  }, [project, projects]);

  const needle = query.trim().toLowerCase();
  const scoped = useMemo(() => inSource.filter((r) => !project || repoProject(r) === project), [inSource, project]);

  const rows = useMemo<Row[]>(() => {
    const matches = scoped.filter((repo) => {
      if (!needle) return true;
      return `${repo.name} ${repoShortName(repo)} ${repoProject(repo)} ${repo.url} ${repo.default_branch} ${repo.description}`.toLowerCase().includes(needle);
    });
    // Name matches rank above matches in the URL/description; then alphabetical.
    const score = (repo: Repository) => {
      if (!needle) return 0;
      const name = repoShortName(repo).toLowerCase();
      if (name === needle) return 0;
      if (name.startsWith(needle)) return 1;
      if (name.includes(needle)) return 2;
      return 3;
    };
    matches.sort((a, b) => score(a) - score(b) || repoProject(a).localeCompare(repoProject(b)) || repoShortName(a).localeCompare(repoShortName(b)));

    const out: Row[] = [];
    if (allowAll && !needle) out.push({ kind: "all" });
    const byId = new Map(matches.map((r) => [r.repo_id, r]));
    if (!needle) {
      const pinnedRepos = pinned.map((id) => byId.get(id)).filter((r): r is Repository => !!r);
      if (pinnedRepos.length) {
        out.push({ kind: "header", label: "Pinned" });
        for (const repo of pinnedRepos) out.push({ kind: "repo", repo, key: `pin:${repo.repo_id}` });
      }
      const recentRepos = recent.map((id) => byId.get(id)).filter((r): r is Repository => !!r && !pinned.includes(r.repo_id));
      if (recentRepos.length) {
        out.push({ kind: "header", label: "Recent" });
        for (const repo of recentRepos) out.push({ kind: "repo", repo, key: `recent:${repo.repo_id}` });
      }
    }
    let current = "";
    let count = 0;
    for (const repo of matches) {
      if (count >= ROW_LIMIT) break;
      const group = needle ? `Matches` : repoProject(repo);
      if (group !== current) {
        current = group;
        out.push({ kind: "header", label: group, count: needle ? matches.length : scoped.filter((r) => repoProject(r) === group).length });
      }
      out.push({ kind: "repo", repo, key: repo.repo_id });
      count += 1;
    }
    return out;
  }, [scoped, needle, pinned, recent, allowAll]);

  const total = useMemo(() => (needle ? scoped.filter((r) => `${r.name} ${repoShortName(r)} ${repoProject(r)} ${r.url} ${r.default_branch} ${r.description}`.toLowerCase().includes(needle)).length : scoped.length), [scoped, needle]);
  const selectable = rows.map((row, index) => (row.kind === "header" ? -1 : index)).filter((i) => i >= 0);

  useEffect(() => setActive(selectable[0] ?? 0), [needle, source, project, open]);

  useEffect(() => {
    if (!open) return;
    const onDown = (event: MouseEvent) => {
      if (!boxRef.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  useEffect(() => {
    listRef.current?.querySelector(`[data-index="${active}"]`)?.scrollIntoView({ block: "nearest" });
  }, [active]);

  function choose(repoId: string) {
    if (repoId) {
      const next = [repoId, ...recent.filter((id) => id !== repoId)].slice(0, RECENT_MAX);
      setRecent(next);
      writeIds(RECENT_KEY, next);
    }
    onChange(repoId);
    setQuery("");
    setOpen(false);
    inputRef.current?.blur();
  }

  function togglePin(repoId: string) {
    const next = pinned.includes(repoId) ? pinned.filter((id) => id !== repoId) : [...pinned, repoId];
    setPinned(next);
    writeIds(PIN_KEY, next);
  }

  function onKey(event: React.KeyboardEvent<HTMLInputElement>) {
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      event.preventDefault();
      if (!open) return setOpen(true);
      const at = selectable.indexOf(active);
      const step = event.key === "ArrowDown" ? 1 : -1;
      const next = selectable[Math.min(selectable.length - 1, Math.max(0, at + step))];
      if (next !== undefined) setActive(next);
    } else if (event.key === "Enter") {
      event.preventDefault();
      const row = rows[active];
      if (row?.kind === "repo") choose(row.repo.repo_id);
      else if (row?.kind === "all") choose("");
    } else if (event.key === "Escape") {
      setOpen(false);
      setQuery("");
    }
  }

  const searchHint = project ? `Search in ${project} (${scoped.length})…` : `Search ${scoped.length} repositories…`;
  const placeholder = open ? searchHint : allowAll ? "All repositories" : "Choose a repository…";

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap items-center gap-1.5">
        <button type="button" className={`btn h-7 px-2.5 text-micro ${source === "all" ? "border-primary bg-primary/15 text-primary" : ""}`} onClick={() => setSource("all")}>
          All <span className="font-mono text-faint">({repos.length})</span>
        </button>
        {sources.map(([type, info]) => (
          <button key={type} type="button" className={`btn h-7 px-2.5 text-micro ${source === type ? "border-primary bg-primary/15 text-primary" : ""}`} onClick={() => setSource((cur) => (cur === type ? "all" : type))}>
            <SourceBadge source={type} label={info.label} />
            <span className="font-mono text-faint">({info.count})</span>
          </button>
        ))}
      </div>
      <div className="flex flex-wrap items-start gap-2">
        <label className="block w-56">
          <span className="sr-only">Project</span>
          <Select className="field" aria-label="Project" value={project} onChange={(e) => setProject(e.target.value)}>
            <option value="">All projects ({projects.length})</option>
            {projects.map(([name, count]) => <option key={name} value={name}>{`${name} (${count})`}</option>)}
          </Select>
        </label>
        <div ref={boxRef} className="relative min-w-[18rem] flex-1">
          <span className="material-symbols-outlined icon-sm pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-faint">search</span>
          <input
            ref={inputRef}
            role="combobox"
            aria-expanded={open}
            aria-label="Repository"
            className="field pl-7 pr-14"
            placeholder={placeholder}
            value={open ? query : selected ? `${repoProject(selected)} / ${repoShortName(selected)}` : ""}
            onFocus={() => setOpen(true)}
            onClick={() => setOpen(true)}
            onChange={(e) => {
              setQuery(e.target.value);
              setOpen(true);
            }}
            onKeyDown={onKey}
          />
          <div className="absolute right-1 top-1/2 flex -translate-y-1/2 items-center">
            {selected && allowAll && (
              <button type="button" className="icon-btn" title="Show all repositories" onClick={() => choose("")}>
                <span className="material-symbols-outlined icon-sm">close</span>
              </button>
            )}
            <button type="button" className="icon-btn" title="Open list" tabIndex={-1} onMouseDown={(e) => e.preventDefault()} onClick={() => (open ? setOpen(false) : inputRef.current?.focus())}>
              <span className="material-symbols-outlined icon-sm">{open ? "expand_less" : "expand_more"}</span>
            </button>
          </div>
          {open && (
            <div className="absolute left-0 right-0 top-full z-30 mt-1 border border-line bg-surface shadow-lg">
              <div ref={listRef} role="listbox" className="max-h-96 overflow-auto py-1">
                {rows.map((row, index) => {
                  if (row.kind === "header") {
                    return (
                      <div key={`h:${row.label}:${index}`} className="sticky top-0 z-[1] flex items-center justify-between bg-surface px-3 pb-0.5 pt-2 font-mono text-micro uppercase text-faint">
                        <span>{row.label === "Pinned" ? "★ Pinned" : row.label}</span>
                        {row.count !== undefined && <span>{row.count}</span>}
                      </div>
                    );
                  }
                  const isActive = index === active;
                  if (row.kind === "all") {
                    return (
                      <div key="all" data-index={index} role="option" aria-selected={!value} className={`flex cursor-pointer items-center gap-2 px-3 py-1.5 text-body-md ${isActive ? "bg-elevated" : ""} ${!value ? "text-primary" : ""}`} onMouseEnter={() => setActive(index)} onMouseDown={(e) => e.preventDefault()} onClick={() => choose("")}>
                        <span className="material-symbols-outlined icon-sm">select_all</span>All repositories
                      </div>
                    );
                  }
                  const { repo } = row;
                  const isPinned = pinned.includes(repo.repo_id);
                  return (
                    <div
                      key={row.key}
                      data-index={index}
                      role="option"
                      aria-selected={repo.repo_id === value}
                      className={`group flex cursor-pointer items-center gap-2 px-3 py-1.5 ${isActive ? "bg-elevated" : ""} ${repo.repo_id === value ? "text-primary" : ""}`}
                      onMouseEnter={() => setActive(index)}
                      onMouseDown={(e) => e.preventDefault()}
                      onClick={() => choose(repo.repo_id)}
                    >
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-mono text-code"><Highlight text={repoShortName(repo)} query={needle} /></div>
                        <div className="truncate text-micro text-faint">
                          <Highlight text={repoProject(repo)} query={needle} /> · {repo.provider_label || repo.source_type}
                          {repo.default_branch && ` · ${repo.default_branch}`}
                        </div>
                      </div>
                      <span className="shrink-0 font-mono text-micro text-faint" title="Last analysis">{repo.latest_analysis ? timeAgo(repo.latest_analysis.created_at) : "not analysed"}</span>
                      <button
                        type="button"
                        className={`icon-btn shrink-0 ${isPinned ? "!text-warning" : "opacity-0 group-hover:opacity-100"}`}
                        title={isPinned ? "Unpin" : "Pin to top"}
                        onClick={(e) => {
                          e.stopPropagation();
                          togglePin(repo.repo_id);
                        }}
                      >
                        <span className="material-symbols-outlined icon-sm" style={isPinned ? { fontVariationSettings: "'FILL' 1" } : undefined}>star</span>
                      </button>
                    </div>
                  );
                })}
                {!rows.some((r) => r.kind === "repo") && <div className="px-3 py-3 text-body-sm text-muted">No repository matches “{query.trim()}”{project ? ` in ${project}` : ""}.</div>}
              </div>
              <div className="flex items-center justify-between border-t border-line px-3 py-1 font-mono text-micro text-faint">
                <span>{total > ROW_LIMIT ? `Showing ${ROW_LIMIT} of ${total} — type to narrow` : `${total} repositor${total === 1 ? "y" : "ies"}`}</span>
                <span>↑↓ move · Enter select · Esc close</span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
