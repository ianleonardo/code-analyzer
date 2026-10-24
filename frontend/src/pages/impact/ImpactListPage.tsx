import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { ApiError, api, errorText, formatDateTime, post, timeAgo } from "../../api";
import { useAuth } from "../../auth";
import { BranchPicker } from "../../components/BranchPicker";
import { RepoPicker } from "../../components/RepoPicker";
import { DateRangeFilter, type Page, Pagination, PurgeDialog, dayToIso } from "../../components/ListTools";
import { Empty, ErrorNote, PageHeader, ShortSha, SourceBadge, Spinner, StatusBadge } from "../../components/ui";
import { can } from "../../permissions";
import type { GitRef, ImpactRunBrief, PullRequestList, Repository } from "../../types";
import { RiskBadge } from "./shared";

const PAGE_SIZE = 50;

export function ImpactListPage() {
  const { user } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const repoId = params.get("repo") ?? "";
  const [repos, setRepos] = useState<Repository[]>([]);
  const [runs, setRuns] = useState<ImpactRunBrief[]>([]);
  const [total, setTotal] = useState(0);
  const [page, setPage] = useState(1);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [purging, setPurging] = useState(false);
  const [notice, setNotice] = useState("");
  const [prs, setPrs] = useState<PullRequestList | null>(null);
  const [refs, setRefs] = useState<GitRef[]>([]);
  const [defaultRef, setDefaultRef] = useState("");
  const [base, setBase] = useState("");
  const [head, setHead] = useState("");
  const [loadingPrs, setLoadingPrs] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [refsNote, setRefsNote] = useState("");
  const canWrite = can(user, "repositories", "write");
  const repo = useMemo(() => repos.find((r) => r.repo_id === repoId) ?? null, [repos, repoId]);

  const loadRuns = useCallback(() => {
    const qs = new URLSearchParams({ page: String(page), page_size: String(PAGE_SIZE) });
    if (repoId) qs.set("repo_id", repoId);
    if (from) qs.set("date_from", dayToIso(from));
    if (to) qs.set("date_to", dayToIso(to, true));
    api<Page<ImpactRunBrief>>(`/api/impact-runs/page?${qs}`)
      .then((data) => {
        setRuns(data.items);
        setTotal(data.total);
      })
      .catch((err) => setError(errorText(err)));
  }, [repoId, page, from, to]);

  useEffect(() => setPage(1), [repoId, from, to]);

  useEffect(() => {
    api<Repository[]>("/api/repositories").then(setRepos).catch((err) => setError(errorText(err)));
  }, []);
  useEffect(() => loadRuns(), [loadRuns]);
  useEffect(() => {
    if (!runs.some((r) => r.status === "running" || r.status === "queued")) return;
    const timer = setInterval(loadRuns, 2500);
    return () => clearInterval(timer);
  }, [runs, loadRuns]);

  useEffect(() => {
    setPrs(null);
    setRefs([]);
    setBase("");
    setHead("");
    setRefsNote("");
    if (!repoId) return;
    setLoadingPrs(true);
    api<PullRequestList>(`/api/repositories/${repoId}/pull-requests`)
      .then(setPrs)
      .catch((err) => setPrs({ supported: true, provider: "", pull_requests: [], message: errorText(err) }))
      .finally(() => setLoadingPrs(false));
    api<{ default_branch: string; refs: GitRef[]; message?: string }>(`/api/repositories/${repoId}/refs`)
      .then((data) => {
        setRefs(data.refs);
        setDefaultRef(data.default_branch);
        setBase(data.default_branch);
        setRefsNote(data.message ?? "");
      })
      .catch((err) => setRefsNote(errorText(err)));
  }, [repoId]);

  async function start(body: Record<string, unknown>, key: string) {
    setBusy(key);
    setError("");
    try {
      const run = await post<ImpactRunBrief>(`/api/repositories/${repoId}/impact-runs`, body);
      navigate(`/impact/${run.run_id}`);
    } catch (err) {
      if (err instanceof ApiError && typeof err.body.run_id === "string") navigate(`/impact/${err.body.run_id}`);
      setError(errorText(err));
    } finally {
      setBusy("");
    }
  }

  const branches = refs.map((r) => ({ name: r.name, sha: r.sha }));

  return (
    <div>
      <PageHeader
        icon="difference"
        title="Pull-request impact"
        subtitle="What a change touches: changed symbols, the code that uses them, affected endpoints, jobs and screens, configuration by environment, tests and risk."
        right={repo && <Link className="btn" to={`/repositories/${repo.repo_id}/features`}><span className="material-symbols-outlined icon-sm">category</span>Feature catalogue</Link>}
      />
      <div className="space-y-4 p-4">
        <ErrorNote message={error} />
        <section className="panel p-3">
          <div className="flex flex-wrap items-end gap-3">
            <div className="block w-full max-w-4xl">
              <span className="label-micro">Repository</span>
              <div className="mt-1">
                <RepoPicker repos={repos} value={repoId} onChange={(id) => setParams(id ? { repo: id } : {})} />
              </div>
            </div>
            {repo && <SourceBadge source={repo.source_type} label={repo.provider_label} />}
          </div>
        </section>

        {repo && (
          <div className="grid gap-4 xl:grid-cols-[1fr_420px]">
            <section className="panel">
              <div className="panel-head"><span>Open pull requests</span>{prs?.supported && <span>{prs.pull_requests.length}</span>}</div>
              {loadingPrs && <div className="p-3"><Spinner label="Reading pull requests…" /></div>}
              {prs && !prs.supported && <p className="p-3 text-body-md text-muted">{prs.message}</p>}
              {prs?.supported && prs.message && <div className="p-3"><ErrorNote message={prs.message} /></div>}
              {prs?.supported && !prs.message && !prs.pull_requests.length && <Empty icon="merge" title="No open pull requests">Compare two branches on the right instead.</Empty>}
              {prs?.supported && prs.pull_requests.length > 0 && (
                <table className="w-full">
                  <thead className="table-head"><tr><th>#</th><th>Pull request</th><th>Branches</th><th>Latest impact</th><th /></tr></thead>
                  <tbody>
                    {prs.pull_requests.map((pr) => (
                      <tr key={pr.number} className="table-row">
                        <td className="font-mono text-code text-faint">{pr.number}</td>
                        <td>
                          <a className="hover:text-primary" href={pr.url} target="_blank" rel="noreferrer">{pr.title}</a>
                          <div className="font-mono text-micro text-faint">{pr.author} · {pr.state} · updated {timeAgo(pr.updated_at)}</div>
                        </td>
                        <td className="font-mono text-micro"><span className="text-primary">{pr.source_branch}</span> → {pr.target_branch}</td>
                        <td>
                          {pr.latest_run ? (
                            <Link to={`/impact/${pr.latest_run.run_id}`} className="inline-flex items-center gap-1">
                              {pr.latest_run.status === "completed" ? <RiskBadge label={pr.latest_run.risk_label} /> : <StatusBadge status={pr.latest_run.status} />}
                              {pr.stale && <span className="chip border-warning/50 text-warning" title="New commits were pushed since this run">stale</span>}
                            </Link>
                          ) : <span className="font-mono text-micro text-faint">not analysed</span>}
                        </td>
                        <td className="text-right">
                          {canWrite && (
                            <button type="button" className="btn" disabled={!!busy} onClick={() => void start({ pr_number: pr.number }, `pr${pr.number}`)}>
                              <span className="material-symbols-outlined icon-sm">play_arrow</span>{busy === `pr${pr.number}` ? "Starting…" : pr.latest_run ? "Re-analyse" : "Analyse impact"}
                            </button>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </section>
            <section className="panel">
              <div className="panel-head"><span>Compare two refs</span></div>
              <div className="space-y-3 p-3">
                <p className="text-body-sm text-muted">The head is diffed against its merge-base with the base, exactly as a pull request would be. Works for local git folders too.</p>
                {refsNote && <p className="text-body-sm text-warning">{refsNote}</p>}
                <div>
                  <span className="label-micro">Base (target)</span>
                  <div className="mt-1"><BranchPicker branches={branches} value={base} defaultBranch={defaultRef} onChange={setBase} /></div>
                </div>
                <div>
                  <span className="label-micro">Head (change)</span>
                  <div className="mt-1"><BranchPicker branches={branches} value={head} defaultBranch={defaultRef} onChange={setHead} /></div>
                </div>
                {canWrite && (
                  <button type="button" className="btn btn-primary w-full justify-center" disabled={!base || !head || base === head || !!busy} onClick={() => void start({ base_ref: base, head_ref: head }, "refs")}>
                    <span className="material-symbols-outlined icon-sm">difference</span>{busy === "refs" ? "Starting…" : "Analyse impact"}
                  </button>
                )}
              </div>
            </section>
          </div>
        )}

        <section className="panel">
          <div className="panel-head"><span>Impact runs</span><span>{total}</span></div>
          <div className="flex flex-wrap items-end justify-between gap-3 border-b border-line p-3">
            <DateRangeFilter from={from} to={to} onChange={(f, t) => { setFrom(f); setTo(t); }} />
            {canWrite && (
              <button type="button" className="btn h-8 hover:!text-critical" onClick={() => setPurging(true)}>
                <span className="material-symbols-outlined icon-sm">delete_sweep</span>Purge by date…
              </button>
            )}
          </div>
          {notice && <p className="border-b border-line px-3 py-2 text-body-sm text-ok">{notice}</p>}
          <table className="w-full">
            <thead className="table-head"><tr><th>Change</th>{!repoId && <th>Repository</th>}<th>Status</th><th>Risk</th><th className="text-right">Files</th><th className="text-right">Entry points</th><th>Head</th><th>Started</th></tr></thead>
            <tbody>
              {runs.map((run) => (
                <tr key={run.run_id} className="table-row">
                  <td><Link className="font-mono text-code hover:text-primary" to={`/impact/${run.run_id}`}>{run.title || `${run.head_ref} → ${run.base_ref}`}</Link></td>
                  {!repoId && <td className="font-mono text-micro">{run.repo_name}</td>}
                  <td><StatusBadge status={run.status} progress={run.progress} /></td>
                  <td><RiskBadge label={run.risk_label} score={run.risk} /></td>
                  <td className="text-right font-mono tnum">{run.stats?.files ?? "—"}{run.stats?.additions !== undefined && <span className="text-micro text-faint"> +{run.stats.additions} −{run.stats.deletions}</span>}</td>
                  <td className="text-right font-mono tnum">{run.stats?.entry_points ?? "—"}</td>
                  <td><ShortSha sha={run.head_sha} /></td>
                  <td className="font-mono text-micro text-faint">{formatDateTime(run.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
          {!runs.length && <Empty icon="difference" title={from || to ? "No impact runs in this date range" : "No impact runs yet"}>{from || to ? "Clear the date filter to see all runs." : "Pick a repository, then analyse an open pull request or compare two branches."}</Empty>}
          <Pagination page={page} pageSize={PAGE_SIZE} total={total} onPage={setPage} />
        </section>
      </div>
      {purging && (
        <PurgeDialog
          title={repo ? `Purge impact runs · ${repo.name}` : "Purge impact runs · all repositories"}
          endpoint="/api/impact-runs/purge"
          noun="impact run(s)"
          extra={repoId ? { repo_id: repoId } : {}}
          initialFrom={from}
          initialTo={to}
          note={repo ? "Only runs for the selected repository are affected." : "Runs for every repository are affected. Pick a repository above to limit the purge."}
          onClose={() => setPurging(false)}
          onDone={(result) => {
            setPurging(false);
            setNotice(`Deleted ${result.deleted} impact run(s)${result.skipped_active ? `; kept ${result.skipped_active} queued/running` : ""}.`);
            if (page === 1) loadRuns();
            else setPage(1);
          }}
        />
      )}
    </div>
  );
}
