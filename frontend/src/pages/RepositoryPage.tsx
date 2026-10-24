import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, api, compact, del, errorText, formatDateTime, post } from "../api";
import { useAuth } from "../auth";
import { BranchPicker } from "../components/BranchPicker";
import { filterFolderFiles, uploadFolderFiles } from "../components/AddRepository";
import { GitSignInDialog } from "../components/GitSignIn";
import { ConfirmDialog, ErrorNote, HealthGauge, Modal, PageHeader, ShortSha, SourceBadge, StatusBadge } from "../components/ui";
import { can } from "../permissions";
import type { AnalysisBrief, Branch, GitCheck, Repository } from "../types";

export function RepositoryPage() {
  const { repoId = "" } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [repo, setRepo] = useState<Repository | null>(null);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [branch, setBranch] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [check, setCheck] = useState<GitCheck | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [deleting, setDeleting] = useState<"repo" | AnalysisBrief | null>(null);
  const [confirmingRun, setConfirmingRun] = useState(false);
  const remote = repo && !["zip", "local"].includes(repo.source_type);
  const canWrite = can(user, "repositories", "write");

  const load = useCallback(async () => {
    const data = await api<Repository>(`/api/repositories/${repoId}`);
    setRepo(data);
    return data;
  }, [repoId]);

  const loadBranches = useCallback(async (data: Repository) => {
    if (["zip", "local"].includes(data.source_type)) return;
    try {
      const listed = await api<{ default_branch: string; branches: Branch[] }>(`/api/repositories/${repoId}/branches`);
      setBranches(listed.branches);
      setBranch((b) => b || data.default_branch || listed.default_branch);
    } catch (err) {
      if (err instanceof ApiError && err.body.status === "auth_required") {
        setCheck(await post<GitCheck>("/api/git/check", { url: data.url }));
      }
      setBranch((b) => b || data.default_branch);
    }
  }, [repoId]);

  useEffect(() => {
    load().then(loadBranches).catch((err) => setError(errorText(err)));
  }, [load, loadBranches]);

  useEffect(() => {
    if (!repo?.analyses?.some((a) => a.status === "running" || a.status === "queued")) return;
    const timer = setInterval(() => void load(), 2500);
    return () => clearInterval(timer);
  }, [repo, load]);

  async function analyse() {
    setBusy(true);
    setError("");
    try {
      const started = await post<AnalysisBrief>(`/api/repositories/${repoId}/analyses`, { branch });
      navigate(`/analyses/${started.analysis_id}`);
    } catch (err) {
      if (err instanceof ApiError && (err.body.status === "auth_required" || err.body.status === "no_access") && repo) {
        setCheck(await post<GitCheck>("/api/git/check", { url: repo.url }));
      }
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function upload(file: File) {
    const form = new FormData();
    form.append("file", file);
    form.append("repo_id", repoId);
    const result = await api<Repository>("/api/repositories/upload", { method: "POST", body: form });
    if (result.started_analysis) navigate(`/analyses/${result.started_analysis.analysis_id}`);
  }

  async function uploadFolder(fileList: FileList) {
    setBusy(true);
    setError("");
    try {
      const { kept } = filterFolderFiles(fileList);
      if (kept.length === 0) {
        setError("No analysable source files were found in the selected folder.");
        return;
      }
      const result = await uploadFolderFiles(kept, { repoId });
      if (result.started_analysis) navigate(`/analyses/${result.started_analysis.analysis_id}`);
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (!repo) return <div className="p-6"><ErrorNote message={error} /></div>;
  const completed = (repo.analyses ?? []).filter((a) => a.status === "completed");

  return (
    <div>
      <PageHeader
        icon="folder_data"
        title={<span className="font-mono">{repo.name}</span>}
        subtitle={
          <span className="flex flex-wrap items-center gap-3">
            <SourceBadge source={repo.source_type} label={repo.provider_label} />
            {repo.web_url && <a className="link font-mono text-code" href={repo.web_url} target="_blank" rel="noreferrer">{repo.web_url}</a>}
            {repo.description && <span>{repo.description}</span>}
          </span>
        }
        right={
          <>
            {repo.source_type !== "zip" && (
              <Link className="btn" to={`/impact?repo=${repo.repo_id}`} title="Impact of a pull request or of one branch against another">
                <span className="material-symbols-outlined icon-sm">difference</span>PR impact
              </Link>
            )}
            <Link className="btn" to={`/repositories/${repo.repo_id}/features`}>
              <span className="material-symbols-outlined icon-sm">category</span>Features
            </Link>
            {canWrite && (
              <button type="button" className="btn btn-danger" onClick={() => setDeleting("repo")}>
                <span className="material-symbols-outlined icon-sm">delete</span>Delete repository
              </button>
            )}
          </>
        }
      />
      <div className="space-y-4 p-4">
        {canWrite && (
          <section className="panel">
            <div className="panel-head"><span>Run analysis</span></div>
            <div className="flex flex-wrap items-end gap-3 p-3">
              {remote ? (
                <div className="w-80">
                  <span className="label-micro">Branch</span>
                  <div className="mt-1"><BranchPicker branches={branches} value={branch} defaultBranch={repo.default_branch} onChange={setBranch} /></div>
                </div>
              ) : repo.source_type === "zip" ? (
                <label className="btn cursor-pointer">
                  <span className="material-symbols-outlined icon-sm">upload</span>Upload a new archive version
                  <input type="file" className="hidden" accept=".zip,.tar.gz,.tgz,.tar" onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])} />
                </label>
              ) : repo.source_type === "local" ? (
                <label className="btn cursor-pointer">
                  <span className="material-symbols-outlined icon-sm">folder_open</span>Select a new folder version
                  <input
                    type="file"
                    multiple
                    className="hidden"
                    {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
                    onChange={(e) => e.target.files && e.target.files.length > 0 && void uploadFolder(e.target.files)}
                  />
                </label>
              ) : null}
              <button type="button" className="btn btn-primary h-8" disabled={busy || (!!remote && !branch)} onClick={() => setConfirmingRun(true)}>
                <span className="material-symbols-outlined icon-sm">play_arrow</span>
                {busy ? "Starting…" : remote ? `Analyse ${branch || "branch"}` : "Re-analyse"}
              </button>
              {check && check.status !== "ok" && (
                <button type="button" className="btn" onClick={() => setCheck({ ...check })}>
                  <span className="material-symbols-outlined icon-sm">login</span>Sign in to {check.repo.provider_label}
                </button>
              )}
            </div>
            <div className="px-3 pb-3"><ErrorNote message={error} /></div>
          </section>
        )}
        <section className="panel">
          <div className="panel-head">
            <span>Analysis history ({repo.analyses?.length ?? 0})</span>
            {can(user, "insights", "write") && (
              <button type="button" className="btn h-6" disabled={selected.length !== 2} onClick={() => navigate(`/compare?left=${selected[0]}&right=${selected[1]}`)}>
                <span className="material-symbols-outlined icon-sm">compare_arrows</span>Compare selected ({selected.length}/2)
              </button>
            )}
          </div>
          <table className="w-full">
            <thead className="table-head">
              <tr><th className="w-8" /><th>Branch · commit</th><th>Status</th><th>Health</th><th className="text-right">Files</th><th className="text-right">LOC</th><th>Style</th><th>Started</th><th>By</th><th /></tr>
            </thead>
            <tbody>
              {(repo.analyses ?? []).map((a) => (
                <tr key={a.analysis_id} className="table-row">
                  <td>
                    <input type="checkbox" disabled={a.status !== "completed"} checked={selected.includes(a.analysis_id)} onChange={(e) => setSelected((s) => (e.target.checked ? [...s, a.analysis_id].slice(-2) : s.filter((x) => x !== a.analysis_id)))} />
                  </td>
                  <td>
                    <Link to={`/analyses/${a.analysis_id}`} className="font-mono text-code text-primary hover:underline">{a.branch || "(archive)"}</Link> <ShortSha sha={a.commit_sha} />
                    {a.commit_message && <div className="max-w-[340px] truncate text-body-sm text-faint">{a.commit_message}</div>}
                  </td>
                  <td><StatusBadge status={a.status} progress={a.progress} /></td>
                  <td><HealthGauge score={a.health_score} size={30} /></td>
                  <td className="text-right font-mono tnum">{compact(a.file_count)}</td>
                  <td className="text-right font-mono tnum">{compact(a.loc_total)}</td>
                  <td className="text-body-sm text-muted">{a.architecture_style || "—"}</td>
                  <td className="font-mono text-micro text-faint">{formatDateTime(a.created_at)}</td>
                  <td className="text-body-sm">{a.started_by_name}</td>
                  <td className="text-right">
                    {canWrite && (
                      <button type="button" className="icon-btn hover:!text-critical" title="Delete analysis" onClick={() => setDeleting(a)}>
                        <span className="material-symbols-outlined icon-sm">delete</span>
                      </button>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {completed.length < 2 && <p className="border-t border-line px-3 py-2 text-body-sm text-faint">Run two branches (for example master and a release branch) to compare them.</p>}
        </section>
      </div>
      {check && check.status !== "ok" && repo && (
        <GitSignInDialog check={check} url={repo.url} onClose={() => setCheck(null)} onConnected={() => { setCheck(null); setError(""); void load().then(loadBranches); }} />
      )}
      {confirmingRun && (
        <Modal
          title="Confirm analysis run"
          onClose={() => !busy && setConfirmingRun(false)}
          width="max-w-md"
          footer={
            <>
              <button type="button" className="btn" disabled={busy} onClick={() => setConfirmingRun(false)}>
                Cancel
              </button>
              <button
                type="button"
                className="btn btn-primary"
                disabled={busy}
                onClick={() => {
                  setConfirmingRun(false);
                  void analyse();
                }}
              >
                <span className="material-symbols-outlined icon-sm">play_arrow</span>
                {busy ? "Starting…" : remote ? `Run analyse (${branch})` : "Run analysis"}
              </button>
            </>
          }
        >
          <div className="space-y-3 text-body-md">
            <div className="flex items-start gap-2.5 border border-warning/40 bg-warning/10 p-3 text-warning">
              <span className="material-symbols-outlined icon-sm mt-0.5">warning</span>
              <div className="space-y-1">
                <div className="font-medium text-ink">
                  {remote ? (
                    <>
                      Start a new analysis on branch <span className="font-mono text-primary">{branch}</span>?
                    </>
                  ) : (
                    <>Re-analyse <span className="font-mono text-primary">{repo.name}</span>?</>
                  )}
                </div>
                <p className="text-body-sm text-muted">
                  This will ingest the codebase, rebuild the dependency graph and architectural layers, and invoke the configured LLM/embedding models (consuming API tokens and worker capacity).
                </p>
              </div>
            </div>
            {remote && (repo.analyses ?? []).some((a) => a.branch === branch) && (
              <p className="text-body-sm text-faint">
                Note: Branch <span className="font-mono text-ink">{branch}</span> already has{" "}
                <span className="font-mono text-ink">
                  {(repo.analyses ?? []).filter((a) => a.branch === branch).length}
                </span>{" "}
                previous analysis run(s) in history.
              </p>
            )}
          </div>
        </Modal>
      )}
      {deleting && (
        <ConfirmDialog
          title={deleting === "repo" ? "Delete repository" : "Delete analysis"}
          message={deleting === "repo" ? "This deletes the repository, every analysis, chat and comparison that uses it." : "This deletes the stored analysis, its chats and comparisons."}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            if (deleting === "repo") {
              await del(`/api/repositories/${repoId}`);
              navigate("/repositories");
            } else {
              await del(`/api/analyses/${deleting.analysis_id}`);
              setDeleting(null);
              void load();
            }
          }}
        />
      )}
    </div>
  );
}
