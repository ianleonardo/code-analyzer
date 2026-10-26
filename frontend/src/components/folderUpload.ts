import { api } from "../api";
import type { Repository } from "../types";

export const EXCLUDED_DIRS = new Set([
  "node_modules", "dist", "build", "out", "target", ".next", ".nuxt", ".svelte-kit", ".output", "__pycache__",
  "coverage", ".nyc_output", ".git", ".svn", ".hg", ".idea", ".vscode", ".vs", ".venv", "venv", ".tox",
  ".mypy_cache", ".pytest_cache", ".ruff_cache", ".gradle", ".terraform", "bower_components", ".cache",
  ".parcel-cache", "storybook-static", "site-packages", "Pods", "DerivedData", ".angular", ".turbo", ".yarn",
  ".pnpm-store", "jspm_packages", "elm-stuff", "_build", "deps", ".dart_tool", ".expo", "vendor",
]);

export const ALLOWED_HIDDEN_DIRS = new Set([".github", ".gitlab", ".circleci", ".devcontainer", ".husky"]);

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
