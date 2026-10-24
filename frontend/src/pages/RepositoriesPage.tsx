import { Fragment, useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, compact, timeAgo } from "../api";
import { useAuth } from "../auth";
import { AddRepositoryDialog } from "../components/AddRepository";
import { Empty, HealthGauge, PageHeader, ShortSha, SourceBadge, StatusBadge } from "../components/ui";
import { can } from "../permissions";
import type { AnalysisBrief, Repository } from "../types";

const SOURCE_ORDER = ["github", "bitbucket", "bitbucket_server", "git", "local", "zip"];

function getProjectName(repo: Repository): string {
  if (repo.owner && repo.owner.trim()) return repo.owner.trim();
  if (repo.name.includes("/")) {
    const prefix = repo.name.split("/")[0]?.trim();
    if (prefix) return prefix;
  }
  if (repo.source_type === "local") return "Local workspace";
  if (repo.source_type === "zip") return "Uploaded archives";
  return repo.host || "Default project";
}

function getRepoDisplayName(repo: Repository): string {
  if (repo.slug && repo.slug.trim()) return repo.slug.trim();
  if (repo.name.includes("/")) {
    const rest = repo.name.split("/").slice(1).join("/").trim();
    if (rest) return rest;
  }
  return repo.name;
}

function getBranchRuns(repo: Repository): AnalysisBrief[] {
  if (repo.branch_runs && repo.branch_runs.length > 0) {
    return repo.branch_runs;
  }
  if (repo.latest_analysis) {
    return [repo.latest_analysis];
  }
  return [];
}

function branchLabel(repo: Repository, branch: string): string {
  if (branch) return branch;
  if (repo.source_type === "local") return "(local snapshot)";
  if (repo.source_type === "zip") return "(archive snapshot)";
  return repo.default_branch || "(default)";
}

type ProjectGroup = {
  projectKey: string;
  projectName: string;
  repos: Repository[];
  branchCount: number;
  avgHealth: number | null;
};

type SourceGroup = {
  sourceType: string;
  providerLabel: string;
  projects: ProjectGroup[];
  repoCount: number;
  branchCount: number;
};

export function RepositoriesPage() {
  const { user } = useAuth();
  const [repos, setRepos] = useState<Repository[] | null>(null);
  const [adding, setAdding] = useState(false);
  const [q, setQ] = useState("");
  const [sourceFilter, setSourceFilter] = useState<string>("all");
  const [collapsedSources, setCollapsedSources] = useState<Record<string, boolean>>({});
  const [collapsedProjects, setCollapsedProjects] = useState<Record<string, boolean>>({});
  const [collapsedRepos, setCollapsedRepos] = useState<Record<string, boolean>>({});

  useEffect(() => {
    const load = () => api<Repository[]>("/api/repositories").then(setRepos).catch(() => setRepos([]));
    load();
    const timer = setInterval(load, 8000);
    return () => clearInterval(timer);
  }, []);

  const filtered = useMemo(() => {
    const query = q.trim().toLowerCase();
    return (repos ?? []).filter((repo) => {
      if (sourceFilter !== "all" && repo.source_type !== sourceFilter) return false;
      if (!query) return true;
      const project = getProjectName(repo);
      const branches = getBranchRuns(repo)
        .map((b) => `${b.branch} ${b.commit_sha} ${b.architecture_style || ""}`)
        .join(" ");
      const haystack = `${repo.name} ${repo.slug} ${project} ${repo.provider_label} ${repo.url} ${repo.default_branch} ${branches}`.toLowerCase();
      return haystack.includes(query);
    });
  }, [repos, q, sourceFilter]);

  const sourceCounts = useMemo(() => {
    const counts = new Map<string, { label: string; count: number }>();
    for (const repo of repos ?? []) {
      const existing = counts.get(repo.source_type);
      if (existing) {
        existing.count += 1;
      } else {
        counts.set(repo.source_type, { label: repo.provider_label, count: 1 });
      }
    }
    return Array.from(counts.entries()).sort(
      (a, b) => (SOURCE_ORDER.indexOf(a[0]) === -1 ? 99 : SOURCE_ORDER.indexOf(a[0])) - (SOURCE_ORDER.indexOf(b[0]) === -1 ? 99 : SOURCE_ORDER.indexOf(b[0])),
    );
  }, [repos]);

  const groups = useMemo<SourceGroup[]>(() => {
    const bySource = new Map<string, { label: string; byProject: Map<string, Repository[]> }>();
    for (const repo of filtered) {
      let src = bySource.get(repo.source_type);
      if (!src) {
        src = { label: repo.provider_label, byProject: new Map() };
        bySource.set(repo.source_type, src);
      }
      const projName = getProjectName(repo);
      const list = src.byProject.get(projName) ?? [];
      list.push(repo);
      src.byProject.set(projName, list);
    }

    const sortedSources = Array.from(bySource.entries()).sort(
      (a, b) => (SOURCE_ORDER.indexOf(a[0]) === -1 ? 99 : SOURCE_ORDER.indexOf(a[0])) - (SOURCE_ORDER.indexOf(b[0]) === -1 ? 99 : SOURCE_ORDER.indexOf(b[0])),
    );

    return sortedSources.map(([sourceType, { label, byProject }]) => {
      let repoCount = 0;
      let sourceBranchCount = 0;
      const projects: ProjectGroup[] = Array.from(byProject.entries())
        .sort((a, b) => a[0].localeCompare(b[0]))
        .map(([projectName, projectRepos]) => {
          const sortedRepos = [...projectRepos].sort((a, b) => getRepoDisplayName(a).localeCompare(getRepoDisplayName(b)));
          repoCount += sortedRepos.length;
          let projBranches = 0;
          const healthScores: number[] = [];
          for (const r of sortedRepos) {
            const runs = getBranchRuns(r);
            projBranches += Math.max(runs.length, r.default_branch ? 1 : 0);
            if (typeof r.latest_analysis?.health_score === "number") {
              healthScores.push(r.latest_analysis.health_score);
            }
          }
          sourceBranchCount += projBranches;
          const avgHealth =
            healthScores.length > 0 ? Math.round(healthScores.reduce((acc, v) => acc + v, 0) / healthScores.length) : null;
          return {
            projectKey: `${sourceType}::${projectName}`,
            projectName,
            repos: sortedRepos,
            branchCount: projBranches,
            avgHealth,
          };
        });

      return {
        sourceType,
        providerLabel: label,
        projects,
        repoCount,
        branchCount: sourceBranchCount,
      };
    });
  }, [filtered]);

  function expandAll() {
    setCollapsedSources({});
    setCollapsedProjects({});
    setCollapsedRepos({});
  }

  function collapseAllBranches() {
    const nextRepos: Record<string, boolean> = {};
    for (const r of repos ?? []) {
      nextRepos[r.repo_id] = true;
    }
    setCollapsedRepos(nextRepos);
  }

  return (
    <div>
      <PageHeader
        icon="folder_data"
        title="Repositories"
        right={
          <>
            <input
              className="field w-64"
              placeholder="Filter source, project, repo, branch…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
            />
            {can(user, "repositories", "write") && (
              <button type="button" className="btn btn-primary" onClick={() => setAdding(true)}>
                <span className="material-symbols-outlined icon-sm">add</span>Add repository
              </button>
            )}
          </>
        }
      />

      <div className="space-y-4 p-4">
        {repos && repos.length > 0 && (
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div className="flex flex-wrap items-center gap-1.5">
              <button
                type="button"
                className={`btn h-7 px-2.5 text-micro ${sourceFilter === "all" ? "border-primary bg-primary/15 text-primary" : ""}`}
                onClick={() => setSourceFilter("all")}
              >
                All sources <span className="font-mono text-faint">({repos.length})</span>
              </button>
              {sourceCounts.map(([srcType, info]) => (
                <button
                  key={srcType}
                  type="button"
                  className={`btn h-7 px-2.5 text-micro ${sourceFilter === srcType ? "border-primary bg-primary/15 text-primary" : ""}`}
                  onClick={() => setSourceFilter((cur) => (cur === srcType ? "all" : srcType))}
                >
                  <SourceBadge source={srcType} label={info.label} />
                  <span className="font-mono text-faint">({info.count})</span>
                </button>
              ))}
            </div>
            <div className="flex items-center gap-1.5">
              <button type="button" className="btn h-7 px-2 text-micro" onClick={expandAll}>
                <span className="material-symbols-outlined icon-sm">unfold_more</span>Expand all
              </button>
              <button type="button" className="btn h-7 px-2 text-micro" onClick={collapseAllBranches}>
                <span className="material-symbols-outlined icon-sm">unfold_less</span>Collapse branches
              </button>
            </div>
          </div>
        )}

        {groups.map((sourceGroup) => {
          const sourceCollapsed = !!collapsedSources[sourceGroup.sourceType];
          return (
            <section key={sourceGroup.sourceType} className="panel overflow-hidden">
              {/* Level 1: Source Type Header */}
              <div
                className="panel-head cursor-pointer select-none hover:bg-hover/50"
                onClick={() =>
                  setCollapsedSources((s) => ({ ...s, [sourceGroup.sourceType]: !s[sourceGroup.sourceType] }))
                }
              >
                <div className="flex items-center gap-2.5">
                  <span className="material-symbols-outlined icon-sm text-muted">
                    {sourceCollapsed ? "chevron_right" : "expand_more"}
                  </span>
                  <SourceBadge source={sourceGroup.sourceType} label={sourceGroup.providerLabel} />
                  <span className="font-mono text-micro text-faint">
                    {sourceGroup.projects.length} {sourceGroup.projects.length === 1 ? "project" : "projects"} ·{" "}
                    {sourceGroup.repoCount} {sourceGroup.repoCount === 1 ? "repo" : "repos"} ·{" "}
                    {sourceGroup.branchCount} {sourceGroup.branchCount === 1 ? "branch" : "branches"}
                  </span>
                </div>
                <span className="font-mono text-micro text-faint">
                  {sourceCollapsed ? "Show group" : "Hide group"}
                </span>
              </div>

              {!sourceCollapsed && (
                <div className="divide-y divide-line">
                  {sourceGroup.projects.map((project) => {
                    const projectCollapsed = !!collapsedProjects[project.projectKey];
                    return (
                      <div key={project.projectKey}>
                        {/* Level 2: Project / Owner Sub-header */}
                        <div
                          className="flex cursor-pointer select-none items-center justify-between bg-bg/60 px-3 py-2 hover:bg-hover/40"
                          onClick={() =>
                            setCollapsedProjects((p) => ({ ...p, [project.projectKey]: !p[project.projectKey] }))
                          }
                        >
                          <div className="flex items-center gap-2 pl-2">
                            <span className="material-symbols-outlined icon-sm text-faint">
                              {projectCollapsed ? "chevron_right" : "expand_more"}
                            </span>
                            <span className="material-symbols-outlined icon-sm text-primary">folder_special</span>
                            <span className="font-mono text-code font-semibold text-ink">{project.projectName}</span>
                            <span className="chip border-line text-faint">
                              {project.repos.length} {project.repos.length === 1 ? "repository" : "repositories"}
                            </span>
                            <span className="chip border-line text-faint">
                              {project.branchCount} {project.branchCount === 1 ? "branch" : "branches"}
                            </span>
                          </div>
                          <div className="flex items-center gap-3">
                            {project.avgHealth !== null && (
                              <div className="flex items-center gap-1.5 text-micro text-muted" title="Average project health">
                                <span>Avg health</span>
                                <HealthGauge score={project.avgHealth} size={24} />
                              </div>
                            )}
                          </div>
                        </div>

                        {/* Level 3 & 4: Repositories and their Branches */}
                        {!projectCollapsed && (
                          <table className="w-full">
                            <thead className="table-head">
                              <tr>
                                <th className="pl-9">Repository / Branch</th>
                                <th>Latest status · Commit</th>
                                <th>Architecture / Details</th>
                                <th>Health</th>
                                <th className="text-right">Scale</th>
                                <th className="text-right">Runs</th>
                                <th>Updated</th>
                              </tr>
                            </thead>
                            <tbody>
                              {project.repos.map((repo) => {
                                const branchRuns = getBranchRuns(repo);
                                const repoCollapsed = !!collapsedRepos[repo.repo_id];
                                const displayName = getRepoDisplayName(repo);
                                return (
                                  <Fragment key={repo.repo_id}>
                                    {/* Level 3: Repository Row */}
                                    <tr className="table-row bg-surface/40">
                                      <td className="max-w-[380px] pl-9">
                                        <div className="flex items-start gap-2">
                                          <button
                                            type="button"
                                            className="mt-0.5 text-muted hover:text-ink"
                                            title={repoCollapsed ? "Expand branches" : "Collapse branches"}
                                            onClick={() =>
                                              setCollapsedRepos((r) => ({ ...r, [repo.repo_id]: !r[repo.repo_id] }))
                                            }
                                          >
                                            <span className="material-symbols-outlined icon-sm">
                                              {repoCollapsed ? "chevron_right" : "expand_more"}
                                            </span>
                                          </button>
                                          <div className="min-w-0">
                                            <div className="flex flex-wrap items-center gap-2">
                                              <span className="material-symbols-outlined icon-sm text-primary">
                                                inventory_2
                                              </span>
                                              <Link
                                                to={`/repositories/${repo.repo_id}`}
                                                className="font-mono text-code font-semibold text-ink hover:text-primary hover:underline"
                                              >
                                                {displayName}
                                              </Link>
                                              {repo.is_private && (
                                                <span className="chip border-line-strong text-faint">private</span>
                                              )}
                                              <span className="chip border-line text-muted">
                                                {branchRuns.length}{" "}
                                                {branchRuns.length === 1 ? "branch" : "branches"}
                                              </span>
                                            </div>
                                            {(repo.description || repo.url) && (
                                              <div className="truncate text-body-sm text-faint">
                                                {repo.description || repo.url}
                                              </div>
                                            )}
                                          </div>
                                        </div>
                                      </td>
                                      <td>
                                        {repo.default_branch ? (
                                          <span className="font-mono text-micro text-muted">
                                            default: <span className="text-primary">{repo.default_branch}</span>
                                          </span>
                                        ) : (
                                          <span className="text-micro text-faint">snapshot</span>
                                        )}
                                      </td>
                                      <td className="text-body-sm text-muted">
                                        {repo.latest_analysis?.architecture_style || "—"}
                                      </td>
                                      <td>
                                        <HealthGauge score={repo.latest_analysis?.health_score} size={28} />
                                      </td>
                                      <td className="text-right font-mono text-micro text-muted tnum">
                                        {repo.latest_analysis
                                          ? `${compact(repo.latest_analysis.file_count)} files · ${compact(repo.latest_analysis.loc_total)} LOC`
                                          : "—"}
                                      </td>
                                      <td className="text-right font-mono tnum">{compact(repo.analysis_count)}</td>
                                      <td>
                                        <div className="flex items-center justify-between gap-2">
                                          <span className="font-mono text-micro text-faint">
                                            {timeAgo(repo.updated_at)}
                                          </span>
                                          <Link
                                            to={`/repositories/${repo.repo_id}`}
                                            className="btn h-6 px-2 text-micro"
                                            title="Manage repository & run analysis"
                                          >
                                            Manage
                                          </Link>
                                        </div>
                                      </td>
                                    </tr>

                                    {/* Level 4: Branch Sub-rows */}
                                    {!repoCollapsed &&
                                      (branchRuns.length > 0 ? (
                                        branchRuns.map((bRun, idx) => {
                                          const isLast = idx === branchRuns.length - 1;
                                          const label = branchLabel(repo, bRun.branch);
                                          const isDefault =
                                            !!repo.default_branch && bRun.branch === repo.default_branch;
                                          return (
                                            <tr
                                              key={bRun.analysis_id}
                                              className="table-row bg-bg/35 hover:bg-hover/60"
                                            >
                                              <td className="pl-16">
                                                <div className="flex items-center gap-2">
                                                  <span className="font-mono text-faint select-none">
                                                    {isLast ? "└─" : "├─"}
                                                  </span>
                                                  <span className="material-symbols-outlined icon-sm text-muted">
                                                    fork_right
                                                  </span>
                                                  <Link
                                                    to={`/analyses/${bRun.analysis_id}`}
                                                    className="font-mono text-code text-primary hover:underline"
                                                  >
                                                    {label}
                                                  </Link>
                                                  {isDefault && (
                                                    <span className="chip border-primary/40 text-primary">
                                                      default
                                                    </span>
                                                  )}
                                                </div>
                                              </td>
                                              <td>
                                                <Link
                                                  to={`/analyses/${bRun.analysis_id}`}
                                                  className="flex flex-wrap items-center gap-2"
                                                >
                                                  <StatusBadge status={bRun.status} progress={bRun.progress} />
                                                  <ShortSha sha={bRun.commit_sha} />
                                                </Link>
                                              </td>
                                              <td className="max-w-[260px]">
                                                <div className="truncate text-body-sm text-muted">
                                                  {bRun.commit_message || bRun.architecture_style || "—"}
                                                </div>
                                              </td>
                                              <td>
                                                <HealthGauge score={bRun.health_score} size={26} />
                                              </td>
                                              <td className="text-right font-mono text-micro text-faint tnum">
                                                {compact(bRun.file_count)} files · {compact(bRun.loc_total)} LOC
                                              </td>
                                              <td className="text-right font-mono text-micro text-faint tnum">
                                                {compact(bRun.run_count ?? 1)} {bRun.run_count === 1 ? "run" : "runs"}
                                              </td>
                                              <td>
                                                <div className="flex items-center justify-between gap-2">
                                                  <span className="font-mono text-micro text-faint">
                                                    {timeAgo(bRun.created_at)}
                                                  </span>
                                                  <Link
                                                    to={`/analyses/${bRun.analysis_id}`}
                                                    className="font-mono text-micro text-primary hover:underline"
                                                  >
                                                    Open →
                                                  </Link>
                                                </div>
                                              </td>
                                            </tr>
                                          );
                                        })
                                      ) : (
                                        <tr className="table-row bg-bg/35">
                                          <td className="pl-16" colSpan={7}>
                                            <div className="flex items-center justify-between py-0.5 text-body-sm text-faint">
                                              <span className="flex items-center gap-2 font-mono">
                                                <span>└─</span>
                                                <span className="material-symbols-outlined icon-sm">fork_right</span>
                                                <span>{repo.default_branch || "(no branch analysed yet)"}</span>
                                              </span>
                                              <Link
                                                to={`/repositories/${repo.repo_id}`}
                                                className="font-mono text-micro text-primary hover:underline"
                                              >
                                                Run first analysis →
                                              </Link>
                                            </div>
                                          </td>
                                        </tr>
                                      ))}
                                  </Fragment>
                                );
                              })}
                            </tbody>
                          </table>
                        )}
                      </div>
                    );
                  })}
                </div>
              )}
            </section>
          );
        })}

        {repos && !groups.length && (
          <div className="panel">
            <Empty icon="folder_data" title="No repositories">
              Add a GitHub, Bitbucket or Git repository (choose the branch), upload an archive, or select a local folder from your browser.
            </Empty>
          </div>
        )}
      </div>
      {adding && <AddRepositoryDialog onClose={() => setAdding(false)} />}
    </div>
  );
}
