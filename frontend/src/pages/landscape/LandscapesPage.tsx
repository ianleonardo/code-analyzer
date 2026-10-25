import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { api, errorText, patch, post, timeAgo } from "../../api";
import { useAuth } from "../../auth";
import { repoProject, repoShortName } from "../../components/RepoPicker";
import { Empty, ErrorNote, Modal, PageHeader, Select, SourceBadge, Spinner } from "../../components/ui";
import { can } from "../../permissions";
import type { Repository } from "../../types";
import type { Landscape } from "./types";

/** Checkbox list of repositories with search, host and project filters; only analysed repositories can be linked. */
export function RepoMultiPicker({ repos, value, onChange }: { repos: Repository[]; value: string[]; onChange: (ids: string[]) => void }) {
  const [query, setQuery] = useState("");
  const [project, setProject] = useState("");
  const [onlySelected, setOnlySelected] = useState(false);
  const chosen = new Set(value);
  const projects = useMemo(() => [...new Set(repos.map(repoProject))].sort(), [repos]);
  const needle = query.trim().toLowerCase();
  const shown = repos
    .filter((r) => !project || repoProject(r) === project)
    .filter((r) => !onlySelected || chosen.has(r.repo_id))
    .filter((r) => !needle || `${r.name} ${repoProject(r)} ${r.url}`.toLowerCase().includes(needle))
    .sort((a, b) => repoProject(a).localeCompare(repoProject(b)) || repoShortName(a).localeCompare(repoShortName(b)));
  const toggle = (id: string) => onChange(chosen.has(id) ? value.filter((v) => v !== id) : [...value, id]);
  return (
    <div className="border border-line">
      <div className="flex flex-wrap items-center gap-2 border-b border-line p-2">
        <div className="relative min-w-[12rem] flex-1">
          <span className="material-symbols-outlined icon-sm pointer-events-none absolute left-2 top-1/2 -translate-y-1/2 text-faint">search</span>
          <input className="field pl-7" placeholder={`Search ${repos.length} repositories…`} value={query} onChange={(e) => setQuery(e.target.value)} />
        </div>
        <Select className="field w-48" value={project} onChange={(e) => setProject(e.target.value)} aria-label="Project">
          <option value="">All projects</option>
          {projects.map((p) => <option key={p} value={p}>{p}</option>)}
        </Select>
        <label className="flex items-center gap-1 text-body-sm text-muted"><input type="checkbox" checked={onlySelected} onChange={(e) => setOnlySelected(e.target.checked)} />Selected only</label>
        <button type="button" className="btn h-7" onClick={() => onChange([...new Set([...value, ...shown.filter((r) => r.analysis_count > 0).map((r) => r.repo_id)])])}>Select shown</button>
        {value.length > 0 && <button type="button" className="btn h-7" onClick={() => onChange([])}>Clear</button>}
      </div>
      <div className="max-h-72 overflow-auto">
        {shown.slice(0, 400).map((r) => {
          const analysed = r.analysis_count > 0;
          return (
            <label key={r.repo_id} className={`flex cursor-pointer items-center gap-2 border-b border-line/50 px-3 py-1.5 ${analysed ? "hover:bg-elevated" : "opacity-50"}`}>
              <input type="checkbox" disabled={!analysed} checked={chosen.has(r.repo_id)} onChange={() => toggle(r.repo_id)} />
              <span className="min-w-0 flex-1">
                <span className="block truncate font-mono text-code">{repoShortName(r)}</span>
                <span className="block truncate text-micro text-faint">{repoProject(r)}{r.default_branch ? ` · ${r.default_branch}` : ""}{analysed ? "" : " · not analysed yet"}</span>
              </span>
              <SourceBadge source={r.source_type} />
            </label>
          );
        })}
        {!shown.length && <p className="p-3 text-body-sm text-muted">No repository matches.</p>}
      </div>
      <div className="border-t border-line px-3 py-1 font-mono text-micro text-faint">{value.length} selected</div>
    </div>
  );
}

export function LandscapeDialog({ landscape, onClose, onSaved }: { landscape?: Landscape; onClose: () => void; onSaved: (l: Landscape) => void }) {
  const [repos, setRepos] = useState<Repository[] | null>(null);
  const [name, setName] = useState(landscape?.name ?? "");
  const [description, setDescription] = useState(landscape?.description ?? "");
  const [policy, setPolicy] = useState<Landscape["auto_accept"]>(landscape?.auto_accept ?? "high_medium");
  const [members, setMembers] = useState<string[]>(landscape?.repos.map((r) => r.repo_id) ?? []);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    api<Repository[]>("/api/repositories").then(setRepos).catch((err) => setError(errorText(err)));
  }, []);

  async function save() {
    setBusy(true);
    setError("");
    try {
      const body = { name, description, auto_accept: policy, repo_ids: members };
      onSaved(landscape ? await patch<Landscape>(`/api/landscapes/${landscape.landscape_id}`, body) : await post<Landscape>("/api/landscapes", body));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={landscape ? `Edit landscape · ${landscape.name}` : "New system landscape"}
      onClose={onClose}
      width="max-w-3xl"
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={busy || !name.trim() || members.length < 2} onClick={() => void save()}>
            {busy ? "Linking…" : landscape ? "Save and relink" : "Create and link"}
          </button>
        </>
      }
    >
      <div className="space-y-3">
        <div className="grid gap-3 md:grid-cols-2">
          <label className="block"><span className="label-micro">Name</span><input className="field mt-1" value={name} placeholder="e.g. Mobile banking" onChange={(e) => setName(e.target.value)} /></label>
          <label className="block"><span className="label-micro">Description</span><input className="field mt-1" value={description} placeholder="optional" onChange={(e) => setDescription(e.target.value)} /></label>
        </div>
        <div>
          <span className="label-micro">Accept links without asking</span>
          <div className="mt-1 grid gap-2 md:grid-cols-2">
            {([["high_medium", "High and medium confidence", "Only ambiguous or low-confidence links go to Review (default)."], ["high", "High confidence only", "Medium-confidence links also wait for your confirmation."]] as const).map(([key, label, hint]) => (
              <label key={key} className={`flex cursor-pointer gap-2 border p-2 ${policy === key ? "border-primary bg-primary/10" : "border-line"}`}>
                <input type="radio" name="policy" checked={policy === key} onChange={() => setPolicy(key)} />
                <span><span className="block text-body-md">{label}</span><span className="block text-body-sm text-muted">{hint}</span></span>
              </label>
            ))}
          </div>
        </div>
        <div>
          <span className="label-micro">Repositories (at least two; the latest analysis of each default branch is used)</span>
          <div className="mt-1">{repos ? <RepoMultiPicker repos={repos} value={members} onChange={setMembers} /> : <Spinner label="Loading repositories…" />}</div>
        </div>
        <ErrorNote message={error} />
      </div>
    </Modal>
  );
}

export function LandscapesPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [list, setList] = useState<Landscape[] | null>(null);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const canWrite = can(user, "insights", "write");

  useEffect(() => {
    api<Landscape[]>("/api/landscapes").then(setList).catch((err) => setError(errorText(err)));
  }, []);

  return (
    <div>
      <PageHeader
        icon="hub"
        title="System landscapes"
        subtitle="End-to-end links between repositories: screens and clients calling APIs in other repositories, Kafka / MQ topics, shared database tables and external systems — with a review queue for anything doubtful."
        right={canWrite && <button type="button" className="btn btn-primary" onClick={() => setCreating(true)}><span className="material-symbols-outlined icon-sm">add</span>New landscape</button>}
      />
      <div className="space-y-4 p-4">
        <ErrorNote message={error} />
        {!list && !error && <Spinner label="Loading…" />}
        {list && !list.length && (
          <section className="panel">
            <Empty icon="hub" title="No landscapes yet">Group the repositories that work together (e.g. an Android app, its APIs and the services behind them); the links are found automatically.</Empty>
          </section>
        )}
        <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
          {(list ?? []).map((l) => (
            <Link key={l.landscape_id} to={`/landscapes/${l.landscape_id}`} className="panel block p-3 hover:border-primary">
              <div className="flex items-start justify-between gap-2">
                <div className="min-w-0">
                  <div className="truncate text-head-md">{l.name}</div>
                  {l.description && <div className="truncate text-body-sm text-muted">{l.description}</div>}
                </div>
                {(l.stats.review ?? 0) > 0 && <span className="chip border-warning/50 text-warning">{l.stats.review} to review</span>}
              </div>
              <div className="mt-3 grid grid-cols-3 gap-2 font-mono">
                <div><div className="text-head-lg">{l.repos.length}</div><div className="label-micro">repos</div></div>
                <div><div className="text-head-lg">{l.stats.links ?? 0}</div><div className="label-micro">links</div></div>
                <div><div className="text-head-lg">{l.stats.external ?? 0}</div><div className="label-micro">external</div></div>
              </div>
              <div className="mt-2 truncate text-micro text-faint">{l.repos.map((r) => r.name).join(" · ")}</div>
              <div className="mt-1 font-mono text-micro text-faint">linked {timeAgo(l.computed_at)} · by {l.created_by_name}</div>
            </Link>
          ))}
        </div>
      </div>
      {creating && <LandscapeDialog onClose={() => setCreating(false)} onSaved={(l) => navigate(`/landscapes/${l.landscape_id}`)} />}
    </div>
  );
}
