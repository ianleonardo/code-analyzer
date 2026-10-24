import { useCallback, useEffect, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { api, del, errorText, patch, post } from "../../api";
import { useAuth } from "../../auth";
import { ConfirmDialog, Empty, ErrorNote, Modal, PageHeader, Select, Spinner } from "../../components/ui";
import { can } from "../../permissions";
import type { Feature, FeatureCatalogue, FeaturePattern, Repository } from "../../types";

const PATTERN_HELP: Record<FeaturePattern["type"], string> = {
  route: "URL prefix or glob, e.g. /payments",
  path: "file glob, e.g. src/payments/**",
  item: "one entry point key",
  name: "regular expression on the entry point name",
};

type Draft = { feature_id?: string; name: string; description: string; patterns: FeaturePattern[]; confirmed: boolean };

export function FeaturesPage() {
  const { repoId = "" } = useParams();
  const { user } = useAuth();
  const canWrite = can(user, "repositories", "write");
  const [repo, setRepo] = useState<Repository | null>(null);
  const [data, setData] = useState<FeatureCatalogue | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [draft, setDraft] = useState<Draft | null>(null);
  const [deleting, setDeleting] = useState<Feature | null>(null);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [pick, setPick] = useState("");

  const load = useCallback(() => {
    api<FeatureCatalogue>(`/api/repositories/${repoId}/features`).then(setData).catch((err) => setError(errorText(err)));
  }, [repoId]);
  useEffect(() => {
    api<Repository>(`/api/repositories/${repoId}`).then(setRepo).catch((err) => setError(errorText(err)));
    load();
  }, [repoId, load]);

  async function suggest() {
    setBusy(true);
    setError("");
    try {
      setData(await post<FeatureCatalogue>(`/api/repositories/${repoId}/features/suggest`, {}));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  async function save() {
    if (!draft) return;
    setError("");
    try {
      const body = { name: draft.name, description: draft.description, patterns: draft.patterns.filter((p) => p.value.trim()), confirmed: draft.confirmed };
      if (draft.feature_id) await patch(`/api/features/${draft.feature_id}`, body);
      else await post(`/api/repositories/${repoId}/features`, body);
      setDraft(null);
      load();
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function confirm(feature: Feature) {
    await patch(`/api/features/${feature.feature_id}`, { confirmed: true });
    load();
  }

  return (
    <div>
      <PageHeader
        icon="category"
        title={<span>Feature catalogue <span className="font-mono text-muted">· {repo?.name}</span></span>}
        subtitle="User-facing capabilities mapped to endpoints, jobs, listeners and screens. PR impact reports group affected entry points by these features."
        right={
          <>
            <Link className="btn" to={`/impact?repo=${repoId}`}><span className="material-symbols-outlined icon-sm">difference</span>PR impact</Link>
            {canWrite && <button type="button" className="btn" disabled={busy} onClick={() => void suggest()}><span className="material-symbols-outlined icon-sm">auto_awesome</span>{busy ? "Proposing…" : "Propose features"}</button>}
            {canWrite && <button type="button" className="btn btn-primary" onClick={() => setDraft({ name: "", description: "", patterns: [{ type: "route", value: "" }], confirmed: true })}><span className="material-symbols-outlined icon-sm">add</span>New feature</button>}
          </>
        }
      />
      <div className="space-y-4 p-4">
        <ErrorNote message={error} />
        {!data && !error && <Spinner label="Loading…" />}
        {data && !data.analysis_id && <section className="panel"><Empty icon="query_stats" title="Analyse the repository first">Features are proposed from the entry points of a completed analysis.</Empty></section>}
        {data && data.analysis_id && (
          <div className="grid gap-4 xl:grid-cols-[1fr_380px]">
            <section className="panel">
              <div className="panel-head"><span>Features · {data.features.length}</span><span>{data.entry_total - data.unassigned.length} of {data.entry_total} entry points assigned</span></div>
              {!data.features.length && <Empty icon="category" title="No features yet">Propose features (LLM when configured, otherwise grouped by route and folder), then confirm or edit them.</Empty>}
              {data.features.map((f) => (
                <div key={f.feature_id} className="border-b border-line/60 px-3 py-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <button type="button" className="icon-btn" onClick={() => setExpanded(expanded === f.feature_id ? null : f.feature_id)}><span className="material-symbols-outlined icon-sm">{expanded === f.feature_id ? "expand_more" : "chevron_right"}</span></button>
                    <span className="text-head-sm">{f.name}</span>
                    {f.confirmed ? <span className="chip border-ok/50 text-ok">confirmed</span> : <span className="chip border-warning/50 text-warning">proposed · {f.source}</span>}
                    <span className="font-mono text-micro text-faint">{f.entry_count ?? 0} entry point(s)</span>
                    <span className="flex-1" />
                    {canWrite && !f.confirmed && <button type="button" className="btn" onClick={() => void confirm(f)}>Confirm</button>}
                    {canWrite && <button type="button" className="btn" onClick={() => setDraft({ feature_id: f.feature_id, name: f.name, description: f.description, patterns: f.patterns.length ? f.patterns : [{ type: "route", value: "" }], confirmed: f.confirmed })}>Edit</button>}
                    {canWrite && <button type="button" className="btn btn-danger" onClick={() => setDeleting(f)}><span className="material-symbols-outlined icon-sm">delete</span></button>}
                  </div>
                  {f.description && <p className="ml-8 text-body-sm text-muted">{f.description}</p>}
                  <div className="ml-8 mt-1 flex flex-wrap gap-1">{f.patterns.map((p, i) => <span key={i} className="chip border-line normal-case text-muted">{p.type}: {p.value}</span>)}</div>
                  {expanded === f.feature_id && (
                    <div className="ml-8 mt-2 space-y-0.5 font-mono text-micro">
                      {(f.entries ?? []).map((e) => <div key={e.item_key}><span className="text-faint">{e.kind}</span> {e.name} <span className="text-faint">{e.path}</span></div>)}
                      {!f.entries?.length && <span className="text-warning">These patterns select no entry point in the latest analysis.</span>}
                    </div>
                  )}
                </div>
              ))}
            </section>
            <section className="panel">
              <div className="panel-head"><span>Unassigned entry points</span><span>{data.unassigned.length}</span></div>
              <div className="max-h-[70vh] overflow-auto">
                {data.unassigned.map((e) => (
                  <div key={e.item_key} className="flex items-center gap-2 border-b border-line/60 px-3 py-1">
                    <div className="min-w-0 flex-1"><div className="truncate font-mono text-code">{e.name}</div><div className="truncate font-mono text-micro text-faint">{e.kind} · {e.path}</div></div>
                  </div>
                ))}
                {!data.unassigned.length && <p className="p-3 text-body-sm text-ok">Every entry point belongs to a feature.</p>}
              </div>
            </section>
          </div>
        )}
      </div>
      {draft && (
        <Modal title={draft.feature_id ? "Edit feature" : "New feature"} onClose={() => setDraft(null)} width="max-w-2xl"
          footer={<><button type="button" className="btn" onClick={() => setDraft(null)}>Cancel</button><button type="button" className="btn btn-primary" disabled={!draft.name.trim()} onClick={() => void save()}>Save</button></>}>
          <div className="space-y-3">
            <label className="block"><span className="label-micro">Name</span><input className="field mt-1" value={draft.name} onChange={(e) => setDraft({ ...draft, name: e.target.value })} /></label>
            <label className="block"><span className="label-micro">Description</span><input className="field mt-1" value={draft.description} onChange={(e) => setDraft({ ...draft, description: e.target.value })} /></label>
            <div>
              <span className="label-micro">Patterns (an entry point belongs to the feature when any pattern matches)</span>
              {draft.patterns.map((p, i) => (
                <div key={i} className="mt-1 flex items-center gap-2">
                  <div className="w-32">
                    <Select className="field" value={p.type} onChange={(e) => setDraft({ ...draft, patterns: draft.patterns.map((x, j) => (j === i ? { ...x, type: e.target.value as FeaturePattern["type"] } : x)) })}>
                      <option value="route">route</option><option value="path">path</option><option value="item">item</option><option value="name">name</option>
                    </Select>
                  </div>
                  <input className="field flex-1 font-mono" placeholder={PATTERN_HELP[p.type]} value={p.value} onChange={(e) => setDraft({ ...draft, patterns: draft.patterns.map((x, j) => (j === i ? { ...x, value: e.target.value } : x)) })} />
                  <button type="button" className="icon-btn" onClick={() => setDraft({ ...draft, patterns: draft.patterns.filter((_, j) => j !== i) })}><span className="material-symbols-outlined icon-sm">close</span></button>
                </div>
              ))}
              <button type="button" className="btn mt-2" onClick={() => setDraft({ ...draft, patterns: [...draft.patterns, { type: "route", value: "" }] })}><span className="material-symbols-outlined icon-sm">add</span>Pattern</button>
            </div>
            {!!data?.unassigned.length && (
              <div>
                <span className="label-micro">Add unassigned entry points</span>
                <input className="field mt-1" placeholder="Filter…" value={pick} onChange={(e) => setPick(e.target.value)} />
                <div className="mt-1 max-h-48 overflow-auto border border-line">
                  {data.unassigned.filter((e) => !pick || `${e.name} ${e.path}`.toLowerCase().includes(pick.toLowerCase())).slice(0, 200).map((e) => {
                    const added = draft.patterns.some((p) => p.type === "item" && p.value === e.item_key);
                    return (
                      <button key={e.item_key} type="button" disabled={added} className="flex w-full items-center gap-2 px-2 py-1 text-left font-mono text-micro hover:bg-surface disabled:opacity-40"
                        onClick={() => setDraft({ ...draft, patterns: [...draft.patterns.filter((p) => p.value), { type: "item", value: e.item_key }] })}>
                        <span className="material-symbols-outlined icon-sm">{added ? "check" : "add"}</span>
                        <span className="text-faint">{e.kind}</span><span className="flex-1 truncate">{e.name}</span><span className="truncate text-faint">{e.path}</span>
                      </button>
                    );
                  })}
                </div>
              </div>
            )}
            <label className="flex items-center gap-2 text-body-md"><input type="checkbox" checked={draft.confirmed} onChange={(e) => setDraft({ ...draft, confirmed: e.target.checked })} />Confirmed</label>
          </div>
        </Modal>
      )}
      {deleting && <ConfirmDialog title="Delete feature" message={`Delete “${deleting.name}”?`} onClose={() => setDeleting(null)} onConfirm={async () => { await del(`/api/features/${deleting.feature_id}`); setDeleting(null); load(); }} />}
    </div>
  );
}
