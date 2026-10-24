import { useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api, compact, errorText, post } from "../api";
import type { Branch, GitCheck, Repository } from "../types";
import { BranchPicker } from "./BranchPicker";
import { GitSignInDialog } from "./GitSignIn";
import { ErrorNote, Field, Modal, Spinner } from "./ui";

type Source = "remote" | "zip" | "local";

const EXCLUDED_DIRS = new Set([
  "node_modules", "dist", "build", "out", "target", ".next", ".nuxt", ".svelte-kit", ".output", "__pycache__",
  "coverage", ".nyc_output", ".git", ".svn", ".hg", ".idea", ".vscode", ".vs", ".venv", "venv", ".tox",
  ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle", ".terraform", "bower_components", ".cache",
  ".parcel-cache", "storybook-static", "site-packages", "Pods", "DerivedData", ".angular", ".turbo", ".yarn",
  ".pnpm-store", "jspm_packages", "elm-stuff", "_build", "deps", ".dart_tool", ".expo", "vendor",
]);

const ALLOWED_HIDDEN_DIRS = new Set([".github", ".gitlab", ".circleci", ".devcontainer", ".husky"]);

export interface FolderSelection {
  folderName: string;
  kept: Array<{ file: File; path: string }>;
  skippedCount: number;
  totalBytes: number;
}

export function filterFolderFiles(fileList: FileList | File[]): FolderSelection {
  const raw = Array.from(fileList);
  let folderName = "";
  const kept: Array<{ file: File; path: string }> = [];
  let skippedCount = 0;
  let totalBytes = 0;

  for (const file of raw) {
    const rel = ((file as File & { webkitRelativePath?: string }).webkitRelativePath || file.name)
      .replace(/\\/g, "/")
      .replace(/^\/+/, "");
    const parts = rel.split("/").filter(Boolean);
    if (!folderName && parts.length > 1) {
      folderName = parts[0];
    }
    const fileName = parts[parts.length - 1] || file.name;
    if (fileName === ".DS_Store" || fileName === "Thumbs.db") {
      skippedCount += 1;
      continue;
    }
    const dirParts = parts.slice(parts.length > 1 ? 1 : 0, -1);
    const shouldSkipDir = dirParts.some(
      (dir) => EXCLUDED_DIRS.has(dir) || (dir.startsWith(".") && !ALLOWED_HIDDEN_DIRS.has(dir)),
    );
    if (shouldSkipDir) {
      skippedCount += 1;
      continue;
    }
    kept.push({ file, path: rel });
    totalBytes += file.size;
  }

  return { folderName, kept, skippedCount, totalBytes };
}

export async function uploadFolderFiles(
  kept: Array<{ file: File; path: string }>,
  opts: { name?: string; repoId?: string } = {},
): Promise<Repository> {
  const form = new FormData();
  if (opts.name) form.append("name", opts.name);
  if (opts.repoId) form.append("repo_id", opts.repoId);
  for (const item of kept) {
    form.append("files", item.file, item.path);
    form.append("paths", item.path);
  }
  return api<Repository>("/api/repositories/upload-folder", { method: "POST", body: form });
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
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="button" className="btn btn-primary" disabled={!canSubmit || busy} onClick={() => void submit()}>
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
              <button key={key} type="button" className={`flex h-9 items-center justify-center gap-1.5 font-mono text-code ${source === key ? "bg-primary/10 text-primary" : "text-muted hover:text-ink"}`} onClick={() => { setSource(key); setError(""); }}>
                <span className="material-symbols-outlined icon-sm">{icon}</span>
                {label}
              </button>
            ))}
          </div>

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
    </>
  );
}
