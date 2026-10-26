import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api, compact, errorText, post, timeAgo } from "../api";
import type { Branch, GitCheck, Repository } from "../types";
import { BranchPicker } from "./BranchPicker";
import { GitSignInDialog } from "./GitSignIn";
import { filterFolderFiles, uploadFolderFiles, type FolderSelection } from "./folderUpload";
import { ErrorNote, Field, HealthGauge, Modal, ShortSha, SourceBadge, Spinner } from "./ui";

type Source = "remote" | "zip" | "local";

export interface ExistingCheckResult {
  exists: boolean;
  repo?: {
    repo_id: string;
    name: string;
    source_type: string;
    url: string;
    default_branch: string;
    created_at: string;
  };
  latest_analysis?: {
    analysis_id: string;
    branch: string;
    commit_sha: string;
    commit_message: string;
    commit_date: string;
    health_score: number | null;
    file_count: number;
    loc_total: number;
    created_at: string;
  };
  version_status?: "same" | "newer" | "older" | "different" | "existing_repo_no_runs";
  version_label?: string;
  message?: string;
  differences?: {
    current_branch?: string;
    existing_branch?: string;
    current_sha?: string;
    existing_sha?: string;
    current_file_count?: number;
    existing_file_count?: number;
  };
}

function ExistPromptModal({
  info,
  onClose,
  onProceed,
  busy,
}: {
  info: ExistingCheckResult;
  onClose: () => void;
  onProceed: () => void;
  busy: boolean;
}) {
  const navigate = useNavigate();
  const repo = info.repo;
  const analysis = info.latest_analysis;
  const status = info.version_status;

  const statusConfig = {
    same: {
      badgeCls: "border-ok/40 bg-ok/10 text-ok",
      icon: "check_circle",
      title: "Same Version",
      desc: "This repository and version has already been analysed in the workspace.",
    },
    newer: {
      badgeCls: "border-primary/40 bg-primary/10 text-primary",
      icon: "upgrade",
      title: "Newer Version",
      desc: "The commit / version you are registering is newer than the previous analysis.",
    },
    older: {
      badgeCls: "border-warning/40 bg-warning/10 text-warning",
      icon: "history",
      title: "Older Version",
      desc: "The commit / version you are registering is older than the existing analysis.",
    },
    different: {
      badgeCls: "border-line-strong bg-elevated text-ink",
      icon: "difference",
      title: "Different Version / Branch",
      desc: "A different commit or branch is being registered for an existing repository.",
    },
    existing_repo_no_runs: {
      badgeCls: "border-line-strong bg-elevated text-muted",
      icon: "info",
      title: "Repository Exists",
      desc: "This repository is already registered, but has no completed runs yet.",
    },
  }[status || "different"] || {
    badgeCls: "border-line-strong bg-elevated text-ink",
    icon: "info",
    title: info.version_label || "Repository Exists",
    desc: info.message || "",
  };

  return (
    <Modal
      title="Repository Already Exists"
      onClose={onClose}
      width="max-w-lg"
      footer={
        <>
          <button type="button" className="btn" onClick={onClose} disabled={busy}>
            Cancel
          </button>
          {repo && (
            <button
              type="button"
              className="btn"
              onClick={() => {
                onClose();
                navigate(analysis ? `/analyses/${analysis.analysis_id}` : `/repositories/${repo.repo_id}`);
              }}
              disabled={busy}
            >
              <span className="material-symbols-outlined icon-sm">open_in_new</span>
              Open existing repository
            </button>
          )}
          <button
            type="button"
            className="btn btn-primary"
            disabled={busy}
            onClick={onProceed}
          >
            <span className="material-symbols-outlined icon-sm">play_arrow</span>
            {busy ? "Starting…" : "Proceed & re-analyse"}
          </button>
        </>
      }
    >
      <div className="space-y-4">
        {/* Status banner */}
        <div className={`flex items-start gap-3 border p-3 ${statusConfig.badgeCls}`}>
          <span className="material-symbols-outlined mt-0.5 icon-md shrink-0">{statusConfig.icon}</span>
          <div className="min-w-0 flex-1">
            <div className="flex items-center gap-2">
              <span className="font-mono font-semibold text-code">{statusConfig.title}</span>
              {repo && <SourceBadge source={repo.source_type} />}
            </div>
            <p className="mt-1 text-body-sm text-ink">{info.message}</p>
            <p className="mt-0.5 font-mono text-micro opacity-80">{statusConfig.desc}</p>
          </div>
        </div>

        {/* Comparison card */}
        <div className="border border-line bg-surface/50 p-3">
          <div className="label-micro mb-2 text-faint">Version comparison</div>
          <div className="grid grid-cols-2 gap-3 text-body-sm">
            <div className="border-r border-line pr-3">
              <div className="font-mono text-micro font-semibold text-primary">NEW REGISTRATION</div>
              <div className="mt-1 space-y-1">
                <div>
                  <span className="text-faint text-micro">Branch: </span>
                  <span className="font-mono text-code text-ink">{info.differences?.current_branch || "—"}</span>
                </div>
                {info.differences?.current_sha && (
                  <div>
                    <span className="text-faint text-micro">Commit: </span>
                    <ShortSha sha={info.differences.current_sha} />
                  </div>
                )}
                {typeof info.differences?.current_file_count === "number" && (
                  <div>
                    <span className="text-faint text-micro">Files: </span>
                    <span className="font-mono text-code text-ink">{compact(info.differences.current_file_count)}</span>
                  </div>
                )}
              </div>
            </div>

            <div>
              <div className="font-mono text-micro font-semibold text-muted">PREVIOUS ANALYSIS</div>
              {analysis ? (
                <div className="mt-1 space-y-1">
                  <div>
                    <span className="text-faint text-micro">Branch: </span>
                    <span className="font-mono text-code text-ink">{analysis.branch || "—"}</span>
                  </div>
                  {analysis.commit_sha && (
                    <div>
                      <span className="text-faint text-micro">Commit: </span>
                      <ShortSha sha={analysis.commit_sha} />
                    </div>
                  )}
                  {analysis.file_count && (
                    <div>
                      <span className="text-faint text-micro">Files: </span>
                      <span className="font-mono text-code text-ink">
                        {compact(analysis.file_count)} {analysis.loc_total ? `(${compact(analysis.loc_total)} LOC)` : ""}
                      </span>
                    </div>
                  )}
                  {analysis.health_score !== null && analysis.health_score !== undefined && (
                    <div className="flex items-center gap-1.5 pt-0.5">
                      <span className="text-faint text-micro">Health: </span>
                      <HealthGauge score={analysis.health_score} size={22} />
                    </div>
                  )}
                  <div className="font-mono text-micro text-faint pt-0.5">
                    Analysed {timeAgo(analysis.created_at)}
                  </div>
                </div>
              ) : (
                <div className="mt-1 text-faint text-micro italic">No analyses recorded yet</div>
              )}
            </div>
          </div>
        </div>

        <p className="text-body-sm text-faint">
          Would you like to run a new analysis anyway, or navigate directly to the existing repository?
        </p>
      </div>
    </Modal>
  );
}

function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const STATUS_STYLE: Record<string, string> = {
  ok: "border-ok/50 bg-ok/10 text-ok",
  auth_required: "border-warning/50 bg-warning/10 text-warning",
  no_access: "border-critical/50 bg-critical/10 text-critical",
  not_found: "border-critical/50 bg-critical/10 text-critical",
  error: "border-critical/50 bg-critical/10 text-critical",
};

/** Register a repository: GitHub / Bitbucket / Git URL (with access check, sign-in popup and branch choice), archive upload, or browser local folder upload. */
export function AddRepositoryDialog({ onClose }: { onClose: () => void }) {
  const navigate = useNavigate();
  const folderInputRef = useRef<HTMLInputElement | null>(null);
  const [source, setSource] = useState<Source>("remote");
  const [url, setUrl] = useState("");
  const [check, setCheck] = useState<GitCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [branches, setBranches] = useState<Branch[]>([]);
  const [branch, setBranch] = useState("");
  const [signIn, setSignIn] = useState(false);
  const [file, setFile] = useState<File | null>(null);
  const [folderSelection, setFolderSelection] = useState<FolderSelection | null>(null);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [existingCheck, setExistingCheck] = useState<ExistingCheckResult | null>(null);
  const [checkingExisting, setCheckingExisting] = useState(false);
  const [promptDialog, setPromptDialog] = useState<ExistingCheckResult | null>(null);

  async function checkExists(): Promise<ExistingCheckResult | null> {
    const selectedBranchSha = branches.find((b) => b.name === branch)?.sha || "";
    const payload: Record<string, any> = {
      source_type: source,
      url: source === "remote" ? url : "",
      branch: source === "remote" ? branch : "",
      commit_sha: selectedBranchSha,
      name: name || (source === "zip" ? file?.name.replace(/\.(zip|tar\.gz|tgz|tar)$/i, "") : folderSelection?.folderName) || "",
      file_count: source === "local" ? folderSelection?.kept.length : undefined,
      total_bytes: source === "local" ? folderSelection?.totalBytes : source === "zip" ? file?.size : undefined,
    };
    if (!payload.url && !payload.name) {
      setExistingCheck(null);
      return null;
    }
    setCheckingExisting(true);
    try {
      const res = await post<ExistingCheckResult>("/api/repositories/check-exists", payload);
      setExistingCheck(res.exists ? res : null);
      return res;
    } catch {
      return null;
    } finally {
      setCheckingExisting(false);
    }
  }

  useEffect(() => {
    if (source === "remote" && check?.status === "ok" && branch) {
      void checkExists();
    } else if (source === "zip" && (file || name)) {
      void checkExists();
    } else if (source === "local" && (folderSelection || name)) {
      void checkExists();
    } else {
      setExistingCheck(null);
    }
  }, [source, url, branch, branches, file, folderSelection, name, check?.status]);

  async function runCheck() {
    setChecking(true);
    setError("");
    setCheck(null);
    setBranches([]);
    try {
      const result = await post<GitCheck>("/api/git/check", { url });
      setCheck(result);
      if (result.status === "ok") {
        setBranch(result.default_branch || "");
        const listed = await post<{ default_branch: string; branches: Branch[] }>("/api/git/branches", { url }).catch(() => null);
        if (listed) {
          setBranches(listed.branches);
          setBranch(result.default_branch || listed.default_branch || listed.branches[0]?.name || "");
        }
      } else if (result.status === "auth_required" || result.status === "no_access") {
        setSignIn(true);
      }
    } catch (err) {
      setError(errorText(err));
    } finally {
      setChecking(false);
    }
  }

  async function submit() {
    setBusy(true);
    setError("");
    try {
      let repo: Repository;
      if (source === "remote") {
        repo = await post<Repository>("/api/repositories", { url, branch, name: name || undefined });
      } else if (source === "zip") {
        const form = new FormData();
        form.append("file", file as File);
        form.append("name", name);
        repo = await api<Repository>("/api/repositories/upload", { method: "POST", body: form });
      } else {
        repo = await uploadFolderFiles(folderSelection?.kept ?? [], { name: name || folderSelection?.folderName });
      }
      onClose();
      navigate(repo.started_analysis ? `/analyses/${repo.started_analysis.analysis_id}` : `/repositories/${repo.repo_id}`);
    } catch (err) {
      if (err instanceof ApiError && (err.body.status === "auth_required" || err.body.status === "no_access")) {
        await runCheck();
      }
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function handleRegisterClick() {
    let checkRes = existingCheck;
    if (!checkRes && canSubmit) {
      checkRes = await checkExists();
    }
    if (checkRes && checkRes.exists) {
      setPromptDialog(checkRes);
      return;
    }
    await submit();
  }

  const canSubmit =
    source === "remote"
      ? check?.status === "ok" && !!branch
      : source === "zip"
        ? !!file
        : (folderSelection?.kept.length ?? 0) > 0;

  return (
    <>
      <Modal
        title="Add repository"
        onClose={onClose}
        width="max-w-xl"
        footer={
          <>
            <button type="button" className="btn" onClick={onClose} disabled={busy}>Cancel</button>
            <button type="button" className="btn btn-primary" disabled={!canSubmit || busy} onClick={() => void handleRegisterClick()}>
              <span className="material-symbols-outlined icon-sm">play_arrow</span>
              {busy ? "Starting…" : "Register & analyse"}
            </button>
          </>
        }
      >
        <div className="space-y-3">
          <div className="grid grid-cols-3 border border-line">
            {([
              ["remote", "hub", "GitHub / Bitbucket / Git"],
              ["zip", "folder_zip", "Upload archive"],
              ["local", "folder_open", "Local folder"],
            ] as const).map(([key, icon, label]) => (
              <button key={key} type="button" className={`flex h-9 items-center justify-center gap-1.5 font-mono text-code ${source === key ? "bg-primary/10 text-primary" : "text-muted hover:text-ink"}`} onClick={() => { setSource(key); setError(""); setExistingCheck(null); }}>
                <span className="material-symbols-outlined icon-sm">{icon}</span>
                {label}
              </button>
            ))}
          </div>

          {checkingExisting && <Spinner label="Checking if repository exists…" />}
          {existingCheck?.exists && (
            <div className="flex items-center justify-between border border-warning/40 bg-warning/10 px-2.5 py-1.5 text-body-sm text-warning">
              <div className="flex items-center gap-2">
                <span className="material-symbols-outlined icon-sm">warning</span>
                <span>
                  <strong>Repository already exists:</strong> {existingCheck.version_label}
                </span>
              </div>
              <button
                type="button"
                className="link text-micro"
                onClick={() => setPromptDialog(existingCheck)}
              >
                View prompt & details →
              </button>
            </div>
          )}

          {source === "remote" && (
            <>
              <Field label="Repository URL" hint="https://github.com/org/repo · https://bitbucket.org/team/repo · https://bitbucket.corp/projects/KEY/repos/name · git@host:org/repo.git">
                <div className="flex gap-2">
                  <input className="field" value={url} placeholder="https://github.com/org/repo" onChange={(e) => { setUrl(e.target.value); setCheck(null); }} onKeyDown={(e) => e.key === "Enter" && url && void runCheck()} />
                  <button type="button" className="btn h-8" disabled={!url || checking} onClick={() => void runCheck()}>
                    <span className="material-symbols-outlined icon-sm">verified_user</span>
                    Check access
                  </button>
                </div>
              </Field>
              {checking && <Spinner label="Checking your access…" />}
              {check && (
                <div className={`border px-2 py-2 text-body-md ${STATUS_STYLE[check.status]}`}>
                  <div className="flex items-center gap-2">
                    <span className="material-symbols-outlined icon-sm">{check.status === "ok" ? "check_circle" : "lock"}</span>
                    <span className="font-mono">{check.repo.provider_label}</span>
                    <span className="font-mono text-ink">{check.repo.name}</span>
                    {check.is_private && <span className="chip border-line-strong text-muted">private</span>}
                    {check.used_credential && <span className="chip border-ok/50 text-ok">your connection</span>}
                  </div>
                  <p className="mt-1 text-muted">{check.message}</p>
                  {check.status !== "ok" && (
                    <button type="button" className="btn mt-2" onClick={() => setSignIn(true)}>
                      <span className="material-symbols-outlined icon-sm">login</span>
                      Sign in to {check.repo.provider_label}
                    </button>
                  )}
                </div>
              )}
              {check?.status === "ok" && (
                <>
                  <Field label="Branch to analyse" hint="Pick master, a release branch or any ref. Each branch keeps its own analysis history.">
                    <BranchPicker branches={branches} value={branch} defaultBranch={check.default_branch || ""} onChange={setBranch} />
                  </Field>
                  <Field label="Display name (optional)">
                    <input className="field" value={name} placeholder={check.repo.name} onChange={(e) => setName(e.target.value)} />
                  </Field>
                </>
              )}
            </>
          )}

          {source === "zip" && (
            <>
              <Field label="Archive" hint=".zip, .tar.gz or .tgz — extracted with Zip-Slip and decompression-bomb guards.">
                <input className="field py-1" type="file" accept=".zip,.tar.gz,.tgz,.tar" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
              </Field>
              <Field label="Name">
                <input className="field" value={name} placeholder={file?.name.replace(/\.(zip|tar\.gz|tgz|tar)$/i, "")} onChange={(e) => setName(e.target.value)} />
              </Field>
            </>
          )}

          {source === "local" && (
            <>
              <Field
                label="Local folder"
                hint="Select a project folder from your computer. Heavy dependency and build folders (node_modules, .git, .venv, dist, build, target) are skipped automatically."
              >
                <input
                  ref={folderInputRef}
                  type="file"
                  multiple
                  className="hidden"
                  {...({ webkitdirectory: "", directory: "" } as Record<string, string>)}
                  onChange={(e) => {
                    if (!e.target.files || e.target.files.length === 0) return;
                    const result = filterFolderFiles(e.target.files);
                    setFolderSelection(result);
                    if (result.kept.length === 0) {
                      setError("No analysable source files were found in the selected folder.");
                    } else {
                      setError("");
                    }
                  }}
                />
                <div className="flex flex-wrap items-center gap-2">
                  <button
                    type="button"
                    className="btn h-8"
                    onClick={() => {
                      if (folderInputRef.current) {
                        folderInputRef.current.value = "";
                        folderInputRef.current.click();
                      }
                    }}
                  >
                    <span className="material-symbols-outlined icon-sm">folder_open</span>
                    {folderSelection ? "Change folder…" : "Select folder…"}
                  </button>
                  {folderSelection && (
                    <span className="font-mono text-code text-ink">
                      {folderSelection.folderName || "Selected folder"}
                    </span>
                  )}
                </div>
                {folderSelection && (
                  <div className="mt-2 flex flex-wrap items-center gap-2 border border-line bg-bg px-2.5 py-2 text-body-sm">
                    <span className="material-symbols-outlined icon-sm text-primary">folder_open</span>
                    <span className="font-mono text-ink">{compact(folderSelection.kept.length)} files</span>
                    <span className="text-faint">·</span>
                    <span className="font-mono text-muted">{formatBytes(folderSelection.totalBytes)}</span>
                    {folderSelection.skippedCount > 0 && (
                      <>
                        <span className="text-faint">·</span>
                        <span className="text-faint">
                          {compact(folderSelection.skippedCount)} ignored (.git / dependencies / build)
                        </span>
                      </>
                    )}
                  </div>
                )}
              </Field>
              <Field label="Name">
                <input
                  className="field"
                  value={name}
                  placeholder={folderSelection?.folderName || "my-service"}
                  onChange={(e) => setName(e.target.value)}
                />
              </Field>
            </>
          )}
          <ErrorNote message={error} />
        </div>
      </Modal>
      {signIn && check && (
        <GitSignInDialog
          check={check}
          url={url}
          onClose={() => setSignIn(false)}
          onConnected={() => {
            setSignIn(false);
            void runCheck();
          }}
        />
      )}
      {promptDialog && (
        <ExistPromptModal
          info={promptDialog}
          onClose={() => setPromptDialog(null)}
          onProceed={async () => {
            setPromptDialog(null);
            await submit();
          }}
          busy={busy}
        />
      )}
    </>
  );
}
