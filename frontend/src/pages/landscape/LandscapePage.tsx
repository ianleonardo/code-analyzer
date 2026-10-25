import { useCallback, useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, del, downloadFile, errorText, post, timeAgo } from "../../api";
import { useAuth } from "../../auth";
import { Mermaid } from "../../components/Mermaid";
import { ConfirmDialog, Empty, ErrorNote, Modal, PageHeader, Select, Spinner, SplitPane } from "../../components/ui";
import { can } from "../../permissions";
import { LandscapeDialog } from "./LandscapesPage";
import { CodeModal, ConfidencePill, KindChip, ReviewDialog, StatusPill } from "./ReviewDialog";
import { MethodBadge } from "../analysis/layers/ItemDetail";
import { ScreenWireframe } from "../analysis/layers/ScreenWireframe";
import type { CatalogItem } from "../analysis/layers/types";
import {
  KIND_META,
  type EntryPoint,
  type ExternalSystem,
  type Journey,
  type Landscape,
  type LandscapeLink,
  type LinkKind,
  type MapEdge,
  type ProvidedInterface,
  type ReviewGroup,
  type ReviewQueue,
  type SystemMap,
} from "./types";

type Tab = "map" | "review" | "interfaces" | "journeys" | "links";
const TABS: [Tab, string, string][] = [
  ["map", "hub", "System map"],
  ["review", "fact_check", "Review"],
  ["interfaces", "lan", "Interfaces"],
  ["journeys", "route", "Journeys"],
  ["links", "list", "All links"],
];
const ALL_KINDS: LinkKind[] = ["http", "messaging", "database", "external"];

export function EndpointLabel({ text }: { text: string }) {
  if (!text) return <span>—</span>;
  const m = text.match(/^(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(.+)$/i);
  if (m) {
    return (
      <span className="inline-flex items-center gap-1.5 text-left max-w-full">
        <MethodBadge method={m[1].toUpperCase()} />
        <span className="truncate font-mono text-code text-left">{m[2]}</span>
      </span>
    );
  }
  return <span className="truncate font-mono text-code text-left">{text}</span>;
}

export function getRepoAnalysisId(landscape: Landscape, repoId: string): string {
  return (
    landscape.stats?.members?.find((m) => m.repo_id === repoId)?.analysis_id ||
    landscape.repos?.find((r) => r.repo_id === repoId)?.pinned_analysis_id ||
    ""
  );
}

export function ScreenMockupModal({
  analysisId,
  itemId,
  itemKey,
  screenName,
  repoName,
  onClose,
  onOpenFile,
}: {
  analysisId: string;
  itemId?: string;
  itemKey?: string;
  screenName: string;
  repoName?: string;
  onClose: () => void;
  onOpenFile?: (path: string, line?: number) => void;
}) {
  const [item, setItem] = useState<CatalogItem | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  useEffect(() => {
    let active = true;
    async function load() {
      setLoading(true);
      setError("");
      try {
        let targetId = itemId;
        if (!targetId && itemKey) {
          const resolved = await api<{ item_id: string }>(
            `/api/analyses/${analysisId}/catalog/resolve?key=${encodeURIComponent(itemKey)}`
          );
          targetId = resolved.item_id;
        }
        if (!targetId) {
          throw new Error("Screen item not found");
        }
        const data = await api<CatalogItem>(
          `/api/analyses/${analysisId}/catalog/items/${targetId}`
        );
        if (active) setItem(data);
      } catch (err) {
        if (active) setError(errorText(err));
      } finally {
        if (active) setLoading(false);
      }
    }
    void load();
    return () => {
      active = false;
    };
  }, [analysisId, itemId, itemKey]);

  return (
    <Modal
      title={
        <div className="flex flex-wrap items-center gap-2">
          <span className="material-symbols-outlined icon-sm text-primary">devices</span>
          <span className="font-semibold text-ink">Screen Mockup</span>
          <span className="font-mono text-body-md text-ink">· {screenName}</span>
          {repoName && (
            <span className="chip border-line text-muted font-mono text-micro">{repoName}</span>
          )}
          {item?.details?.platform && (
            <span className="chip border-line-strong text-muted text-micro">{item.details.platform}</span>
          )}
        </div>
      }
      onClose={onClose}
      width="max-w-5xl"
      footer={
        <div className="flex w-full items-center justify-between">
          <div className="flex items-center gap-2 min-w-0">
            {item?.path && (
              <button
                type="button"
                className="link font-mono text-micro text-muted hover:text-primary truncate"
                onClick={() => onOpenFile?.(item.path, item.line_start || 1)}
                title={`View source code: ${item.path}`}
              >
                <span className="material-symbols-outlined icon-sm align-middle mr-1">code</span>
                {item.path}{item.line_start ? `:${item.line_start}` : ""}
              </button>
            )}
          </div>
          <button type="button" className="btn h-7" onClick={onClose}>
            Close
          </button>
        </div>
      }
    >
      <div className="p-3">
        {loading && <Spinner label="Loading screen mockup data…" />}
        {error && <ErrorNote message={error} />}
        {!loading && !error && item && (
          <ScreenWireframe
            analysisId={analysisId}
            itemId={item.item_id}
            itemName={item.name}
            itemPath={item.path}
            d={item.details || {}}
            onLine={(line) => onOpenFile?.(item.path, line)}
          />
        )}
      </div>
    </Modal>
  );
}

function InterfacesModal({
  edge,
  labelMap,
  landscapeId,
  canWrite,
  onClose,
  onOpenLinks,
  onReviewDecided,
}: {
  edge: MapEdge;
  labelMap: Record<string, string>;
  landscapeId: string;
  canWrite: boolean;
  onClose: () => void;
  onOpenLinks: (repoId: string, kind: string) => void;
  onReviewDecided: () => void;
}) {
  const [q, setQ] = useState("");
  const [code, setCode] = useState<{ analysisId: string; path: string; line: number; title: string } | null>(null);
  const [reviewGroup, setReviewGroup] = useState<ReviewGroup | null>(null);

  const fromName = labelMap[edge.source] ?? edge.source;
  const toName = labelMap[edge.target] ?? edge.target;
  const meta = KIND_META[edge.kind] ?? { label: edge.kind, icon: "hub", color: "" };

  const rawLinks = edge.links ?? [];
  const links = useMemo(() => {
    if (!q.trim()) return rawLinks;
    const needle = q.toLowerCase();
    return rawLinks.filter(
      (l) =>
        l.label?.toLowerCase().includes(needle) ||
        l.source_name?.toLowerCase().includes(needle) ||
        l.target_name?.toLowerCase().includes(needle) ||
        l.source_path?.toLowerCase().includes(needle) ||
        l.target_path?.toLowerCase().includes(needle) ||
        l.features?.some((f) => f.toLowerCase().includes(needle)),
    );
  }, [rawLinks, q]);

  async function openReview(link: LandscapeLink) {
    try {
      const queue = await api<ReviewQueue>(`/api/landscapes/${landscapeId}/review`);
      const all = [...queue.pending, ...queue.decided, ...queue.unresolved];
      const found = all.find((g) => g.group_key === link.group_key);
      if (found) {
        setReviewGroup(found);
        return;
      }
      setReviewGroup({
        group_key: link.group_key,
        kind: link.kind,
        source_repo_id: link.source_repo_id,
        source_repo_name: link.source_repo_name,
        source_name: link.source_name,
        source_kind: link.source_kind,
        source_path: link.source_path,
        source_line: link.source_line,
        label: link.label,
        candidates: [link],
        features: link.features,
        ambiguous: link.ambiguous,
      });
    } catch {
      // fallback
    }
  }

  return (
    <Modal
      title={
        <div className="flex flex-wrap items-center gap-2">
          <span className="material-symbols-outlined icon-sm text-primary">{meta.icon}</span>
          <span>
            Interfaces · <span className="font-mono text-ink">{fromName}</span>{" "}
            <span className="text-muted">→</span> <span className="font-mono text-ink">{toName}</span>
          </span>
          <KindChip kind={edge.kind} />
          <span className="chip border-line text-muted">{rawLinks.length || edge.count} total</span>
          {edge.review > 0 && <span className="chip border-warning/50 text-warning">{edge.review} to review</span>}
        </div>
      }
      onClose={onClose}
      width="max-w-5xl"
      footer={
        <div className="flex w-full items-center justify-between">
          <button
            type="button"
            className="btn"
            onClick={() => {
              onClose();
              onOpenLinks(edge.source.startsWith("ext:") ? edge.target : edge.source, edge.kind);
            }}
          >
            <span className="material-symbols-outlined icon-sm">open_in_new</span>
            View in All Links
          </button>
          <button type="button" className="btn btn-primary" onClick={onClose}>
            Close
          </button>
        </div>
      }
    >
      <div className="space-y-3">
        {rawLinks.length > 3 && (
          <div className="flex items-center gap-2">
            <input
              className="field flex-1"
              placeholder="Search interfaces, methods, paths or features…"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              autoFocus
            />
            {q && (
              <button type="button" className="btn h-7" onClick={() => setQ("")}>
                Clear
              </button>
            )}
          </div>
        )}

        <div className="max-h-[60vh] overflow-auto border border-line">
          <table className="w-full table-fixed">
            <thead className="table-head sticky top-0 z-10 bg-surface">
              <tr>
                <th className="w-10 text-center">#</th>
                <th className="w-[34%]">Interface / Endpoint / Topic</th>
                <th className="w-[28%]">Caller ({fromName})</th>
                <th className="w-[28%]">Target ({toName})</th>
                <th className="w-20 text-right pr-3">Code</th>
              </tr>
            </thead>
            <tbody>
              {links.map((link, idx) => {
                const callerShortPath = link.source_path
                  ? link.source_path.split("/").slice(-2).join("/")
                  : "";
                const targetShortPath = link.target_path
                  ? link.target_path.split("/").slice(-2).join("/")
                  : "";
                return (
                  <tr key={link.link_id || idx} className="table-row">
                    <td className="w-10 text-center font-mono text-micro text-faint">{idx + 1}</td>
                    <td className="w-[34%] overflow-hidden py-2 text-left">
                      <div className="truncate font-mono text-code font-medium text-ink text-left" title={link.label}>
                        <EndpointLabel text={link.label || link.target_name || "—"} />
                      </div>
                      {link.features.length > 0 && (
                        <div className="truncate text-micro text-muted text-left" title={link.features.join(", ")}>
                          {link.features.join(", ")}
                        </div>
                      )}
                    </td>
                    <td className="w-[28%] overflow-hidden py-2 text-left">
                      <div className="truncate font-mono text-code text-ink text-left" title={link.source_name}>
                        <EndpointLabel text={link.source_name || "—"} />
                      </div>
                      {link.source_path && (
                        <div className="truncate text-left">
                          <button
                            type="button"
                            className="link truncate font-mono text-micro text-faint hover:text-primary max-w-full text-left inline-block"
                            onClick={() =>
                              link.source_analysis_id &&
                              setCode({
                                analysisId: link.source_analysis_id,
                                path: link.source_path,
                                line: link.source_line,
                                title: link.source_repo_name || fromName,
                              })
                            }
                            title={`View caller code: ${link.source_path}:${link.source_line}`}
                          >
                            {callerShortPath}{link.source_line ? `:${link.source_line}` : ""}
                          </button>
                        </div>
                      )}
                    </td>
                    <td className="w-[28%] overflow-hidden py-2 text-left">
                      <div className="truncate font-mono text-code text-ink text-left" title={link.target_name || link.external_name}>
                        <EndpointLabel text={link.target_name || link.external_name || "—"} />
                      </div>
                      {link.target_path ? (
                        <div className="truncate text-left">
                          <button
                            type="button"
                            className="link truncate font-mono text-micro text-faint hover:text-primary max-w-full text-left inline-block"
                            onClick={() =>
                              link.target_analysis_id &&
                              setCode({
                                analysisId: link.target_analysis_id,
                                path: link.target_path,
                                line: link.target_line,
                                title: link.target_repo_name || toName,
                              })
                            }
                            title={`View target code: ${link.target_path}:${link.target_line}`}
                          >
                            {targetShortPath}{link.target_line ? `:${link.target_line}` : ""}
                          </button>
                        </div>
                      ) : (
                        link.external_name && (
                          <div className="truncate text-micro text-faint text-left" title={link.external_type}>
                            {link.external_type || "external system"}
                          </div>
                        )
                      )}
                    </td>
                    <td className="w-20 text-right whitespace-nowrap py-2 pr-3">
                      {link.status === "review" && canWrite ? (
                        <button
                          type="button"
                          className="btn h-6 text-warning border-warning/50 hover:bg-warning/15"
                          onClick={() => void openReview(link)}
                        >
                          Review
                        </button>
                      ) : link.source_path && link.source_analysis_id ? (
                        <button
                          type="button"
                          className="btn h-6"
                          title="View source code"
                          onClick={() =>
                            setCode({
                              analysisId: link.source_analysis_id,
                              path: link.source_path,
                              line: link.source_line,
                              title: link.source_repo_name || fromName,
                            })
                          }
                        >
                          <span className="material-symbols-outlined icon-sm">code</span>
                          Code
                        </button>
                      ) : null}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {!links.length && (
            <div className="p-4 text-center text-body-sm text-muted">
              {q ? "No interfaces match the search query." : "No interfaces recorded for this connection."}
            </div>
          )}
        </div>
      </div>

      {code && <CodeModal {...code} onClose={() => setCode(null)} />}
      {reviewGroup && (
        <ReviewDialog
          landscapeId={landscapeId}
          group={reviewGroup}
          canWrite={canWrite}
          onClose={() => setReviewGroup(null)}
          onDecided={() => {
            setReviewGroup(null);
            onReviewDecided();
          }}
        />
      )}
    </Modal>
  );
}

function MapTab({ landscape, onOpenLinks }: { landscape: Landscape; onOpenLinks: (repoId: string, kind: string) => void }) {
  const { user } = useAuth();
  const [kinds, setKinds] = useState<LinkKind[]>(ALL_KINDS);
  const [feature, setFeature] = useState("");
  const [withReview, setWithReview] = useState(true);
  const [data, setData] = useState<SystemMap | null>(null);
  const [error, setError] = useState("");
  const [selectedEdge, setSelectedEdge] = useState<MapEdge | null>(null);

  const loadMap = useCallback(() => {
    const qs = new URLSearchParams({ kinds: kinds.join(","), include_review: String(withReview) });
    if (feature) qs.set("feature", feature);
    api<SystemMap>(`/api/landscapes/${landscape.landscape_id}/map?${qs}`).then(setData).catch((err) => setError(errorText(err)));
  }, [landscape, kinds, feature, withReview]);

  useEffect(loadMap, [loadMap]);
  const label = useMemo(() => Object.fromEntries((data?.nodes ?? []).map((n) => [n.id, n.label])), [data]);

  const handleEdgeClick = useCallback((idx: number, text: string, edgeId?: string) => {
    if (!data?.edges?.length) return;
    let found = edgeId ? data.edges.find((e) => edgeId.includes(e.source) && edgeId.includes(e.target)) : undefined;
    if (!found && idx >= 0 && idx < data.edges.length) {
      found = data.edges[idx];
    }
    if (!found && text) {
      found = data.edges.find((e) => text.toLowerCase().includes(e.kind.toLowerCase()));
    }
    if (found) {
      setSelectedEdge(found);
    }
  }, [data]);

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <div className="flex shrink-0 flex-wrap items-center gap-2 border-b border-line bg-surface px-4 py-2">
        {ALL_KINDS.map((k) => (
          <button
            key={k}
            type="button"
            className={`btn h-7 ${kinds.includes(k) ? "border-primary bg-primary/15 text-primary" : ""}`}
            onClick={() => setKinds(kinds.includes(k) ? kinds.filter((x) => x !== k) : [...kinds, k])}
          >
            <span className="material-symbols-outlined icon-sm">{KIND_META[k].icon}</span>
            {KIND_META[k].label}
          </button>
        ))}
        <Select className="field h-7 w-56" value={feature} onChange={(e) => setFeature(e.target.value)} aria-label="Feature">
          <option value="">All features</option>
          {(data?.features ?? []).map((f) => (
            <option key={f} value={f}>{f}</option>
          ))}
        </Select>
        <label className="flex items-center gap-1.5 text-body-sm text-muted">
          <input type="checkbox" checked={withReview} onChange={(e) => setWithReview(e.target.checked)} />
          Show links waiting for review (dotted)
        </label>
        {data && (
          <span className="ml-auto font-mono text-micro text-faint">
            {data.nodes.length} systems · {data.edges.length} connections
          </span>
        )}
      </div>
      <ErrorNote message={error} />
      {!data && !error && (
        <div className="flex flex-1 items-center justify-center p-6">
          <Spinner label="Drawing…" />
        </div>
      )}
      {data && (
        <div className="min-h-0 flex-1">
          <SplitPane
            direction="vertical"
            primary="first"
            initialSize={480}
            minSize={160}
            maxSize={840}
            firstTitle="System Map"
            secondTitle="Connections Table"
            first={
              <div className="relative flex h-full w-full flex-col overflow-hidden">
                <div className="panel-head shrink-0 pr-9">
                  <span>System Map</span>
                  <span className="text-micro text-muted">Click any arrow link to view interfaces · {data.nodes.length} nodes · {data.edges.length} edges</span>
                </div>
                <div className="relative min-h-0 flex-1 overflow-auto p-4 grid-canvas">
                  {data.edges.length ? (
                    <Mermaid source={data.mermaid} onEdgeClick={handleEdgeClick} />
                  ) : (
                    <Empty icon="hub" title="No links for these filters">
                      Try more link types, or check the Review tab.
                    </Empty>
                  )}
                </div>
              </div>
            }
            second={
              <div className="flex h-full min-h-0 flex-col overflow-hidden">
                <div className="panel-head shrink-0 pr-9">
                  <span>Connections</span>
                  <span>{data.edges.length}</span>
                </div>
                <div className="min-h-0 flex-1 overflow-auto">
                  <table className="w-full">
                    <thead className="table-head sticky top-0 z-10 bg-surface">
                      <tr>
                        <th>From</th>
                        <th>To</th>
                        <th>Type</th>
                        <th className="text-right">Links</th>
                        <th className="text-right">To review</th>
                        <th>Interfaces</th>
                        <th>Features</th>
                      </tr>
                    </thead>
                    <tbody>
                      {data.edges.map((e, i) => (
                        <tr
                          key={i}
                          className="table-row cursor-pointer"
                          onClick={() => setSelectedEdge(e)}
                          title="Click to view interfaces for this connection"
                        >
                          <td className="font-mono text-code">{label[e.source] ?? e.source}</td>
                          <td className="font-mono text-code">{label[e.target] ?? e.target}</td>
                          <td><KindChip kind={e.kind} /></td>
                          <td className="text-right font-mono tnum text-primary font-medium hover:underline">{e.count}</td>
                          <td className="text-right font-mono tnum text-warning">{e.review || ""}</td>
                          <td className="max-w-md truncate font-mono text-micro text-muted">{e.labels.join(" · ")}</td>
                          <td className="text-body-sm text-muted">{e.features.slice(0, 3).join(", ")}</td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                  {!data.edges.length && (
                    <div className="p-4 text-center text-body-sm text-muted">
                      No connections for the active filter.
                    </div>
                  )}
                </div>
              </div>
            }
          />
        </div>
      )}
      {selectedEdge && (
        <InterfacesModal
          edge={selectedEdge}
          labelMap={label}
          landscapeId={landscape.landscape_id}
          canWrite={can(user, "insights", "write")}
          onClose={() => setSelectedEdge(null)}
          onOpenLinks={onOpenLinks}
          onReviewDecided={loadMap}
        />
      )}
    </div>
  );
}

function ReviewTab({ landscape, canWrite, onChanged }: { landscape: Landscape; canWrite: boolean; onChanged: () => void }) {
  const [queue, setQueue] = useState<ReviewQueue | null>(null);
  const [groupBy, setGroupBy] = useState<"feature" | "pair" | "none">("pair");
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [open, setOpen] = useState<ReviewGroup | null>(null);
  const [showDecided, setShowDecided] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    api<ReviewQueue>(`/api/landscapes/${landscape.landscape_id}/review`).then((q) => { setQueue(q); setSelected(new Set()); }).catch((err) => setError(errorText(err)));
  }, [landscape.landscape_id]);
  useEffect(load, [load]);

  const sections = useMemo(() => {
    const out: Record<string, ReviewGroup[]> = {};
    for (const g of queue?.pending ?? []) {
      const keys = groupBy === "feature" ? (g.features.length ? g.features : ["No feature"]) : groupBy === "pair"
        ? [`${g.source_repo_name} → ${[...new Set(g.candidates.map((c) => c.target_repo_name || c.external_name))].join(" / ")}`] : ["All"];
      for (const k of keys) (out[k] ??= []).push(g);
    }
    return Object.entries(out).sort((a, b) => a[0].localeCompare(b[0]));
  }, [queue, groupBy]);

  async function bulk(action: "accept_top" | "reject") {
    setBusy(true);
    setError("");
    try {
      await post(`/api/landscapes/${landscape.landscape_id}/decisions`, { group_keys: [...selected], action });
      load();
      onChanged();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }

  if (!queue) return error ? <ErrorNote message={error} /> : <Spinner label="Loading…" />;
  return (
    <div className="space-y-4">
      <ErrorNote message={error} />
      <section className="panel">
        <div className="panel-head">
          <span>Waiting for review · {queue.pending.length}</span>
          <span className="flex items-center gap-2 normal-case">
            Group by
            <Select className="field h-6 w-44" value={groupBy} onChange={(e) => setGroupBy(e.target.value as typeof groupBy)}>
              <option value="pair">Repository pair</option>
              <option value="feature">Feature</option>
              <option value="none">Nothing</option>
            </Select>
          </span>
        </div>
        {canWrite && selected.size > 0 && (
          <div className="flex items-center gap-2 border-b border-line bg-primary/5 px-3 py-2">
            <span className="text-body-md">{selected.size} selected</span>
            <button type="button" className="btn h-7" disabled={busy} onClick={() => void bulk("accept_top")}>Accept the top suggestion</button>
            <button type="button" className="btn btn-danger h-7" disabled={busy} onClick={() => void bulk("reject")}>Not links</button>
            <button type="button" className="btn btn-ghost h-7" onClick={() => setSelected(new Set())}>Clear</button>
          </div>
        )}
        {!queue.pending.length && <Empty icon="task_alt" title="Nothing to review">Every cross-repository link is either confident enough to accept automatically or already decided.</Empty>}
        {sections.map(([title, groups]) => (
          <div key={title}>
            <div className="flex items-center gap-2 border-b border-line bg-inset px-3 py-1.5 font-mono text-micro uppercase text-muted">
              {canWrite && <input type="checkbox" checked={groups.every((g) => selected.has(g.group_key))}
                onChange={(e) => { const next = new Set(selected); groups.forEach((g) => (e.target.checked ? next.add(g.group_key) : next.delete(g.group_key))); setSelected(next); }} />}
              {title} <span className="text-faint">({groups.length})</span>
            </div>
            {groups.map((g) => {
              const top = [...g.candidates].sort((a, b) => b.score - a.score);
              return (
                <div key={`${title}:${g.group_key}`} className="flex flex-wrap items-center gap-3 border-b border-line/60 px-3 py-2">
                  {canWrite && <input type="checkbox" checked={selected.has(g.group_key)} onChange={() => { const next = new Set(selected); if (next.has(g.group_key)) next.delete(g.group_key); else next.add(g.group_key); setSelected(next); }} />}
                  <KindChip kind={g.kind} />
                  <div className="min-w-0 flex-1">
                    <div className="truncate font-mono text-code"><span className="text-muted">{g.source_repo_name} ·</span> {g.source_name}</div>
                    <div className="truncate text-body-sm text-muted">
                      {top.slice(0, 3).map((c) => `${c.target_repo_name || c.external_name}: ${c.target_name || ""} (${c.score.toFixed(2)})`).join("  ·  ")}
                    </div>
                  </div>
                  {g.ambiguous && <span className="chip border-warning/50 text-warning">ambiguous</span>}
                  {top[0] && <ConfidencePill link={top[0]} />}
                  <button type="button" className="btn h-7" onClick={() => setOpen(g)}>{canWrite ? "Review…" : "View"}</button>
                </div>
              );
            })}
          </div>
        ))}
      </section>

      {queue.unresolved.length > 0 && (
        <section className="panel">
          <div className="panel-head"><span>Calls no repository in the landscape provides · {queue.unresolved.length}</span></div>
          <p className="border-b border-line px-3 py-2 text-body-sm text-muted">Relative URLs whose backend is not in this landscape. Add the repository that serves them, or mark them as external.</p>
          {queue.unresolved.slice(0, 200).map((g) => (
            <div key={g.group_key} className="flex items-center gap-3 border-b border-line/60 px-3 py-1.5">
              <KindChip kind={g.kind} />
              <span className="min-w-0 flex-1 truncate font-mono text-code"><span className="text-muted">{g.source_repo_name} ·</span> {g.source_name}</span>
              <button type="button" className="btn h-7" onClick={() => setOpen(g)}>{canWrite ? "Decide…" : "View"}</button>
            </div>
          ))}
        </section>
      )}

      <section className="panel">
        <button type="button" className="panel-head w-full" onClick={() => setShowDecided(!showDecided)}>
          <span>Decided · {queue.decided.length}</span>
          <span className="material-symbols-outlined icon-sm">{showDecided ? "expand_less" : "expand_more"}</span>
        </button>
        {showDecided && queue.decided.map((g) => (
          <div key={g.group_key} className="flex items-center gap-3 border-b border-line/60 px-3 py-1.5">
            <KindChip kind={g.kind} />
            <span className="min-w-0 flex-1 truncate font-mono text-code"><span className="text-muted">{g.source_repo_name} ·</span> {g.source_name}</span>
            <span className="text-body-sm text-muted">
              {g.decision?.reject ? "not a link" : g.decision?.external ? `external${g.decision.external_name ? `: ${g.decision.external_name}` : ""}` : g.candidates.filter((c) => c.status === "confirmed").map((c) => c.target_repo_name).join(", ")}
            </span>
            <button type="button" className="btn h-7" onClick={() => setOpen(g)}>Change…</button>
          </div>
        ))}
      </section>

      {open && (
        <ReviewDialog landscapeId={landscape.landscape_id} group={open} canWrite={canWrite} onClose={() => setOpen(null)}
          onDecided={() => { setOpen(null); load(); onChanged(); }} />
      )}
    </div>
  );
}

function InterfacesTab({ landscape }: { landscape: Landscape }) {
  const [data, setData] = useState<{ provided: ProvidedInterface[]; external: ExternalSystem[] } | null>(null);
  const [q, setQ] = useState("");
  const [error, setError] = useState("");
  const [code, setCode] = useState<{ analysisId: string; path: string; line: number; title: string } | null>(null);
  useEffect(() => {
    api<{ provided: ProvidedInterface[]; external: ExternalSystem[] }>(`/api/landscapes/${landscape.landscape_id}/interfaces`).then(setData).catch((err) => setError(errorText(err)));
  }, [landscape]);
  const analysisOf = (repoId: string) => getRepoAnalysisId(landscape, repoId);
  const needle = q.trim().toLowerCase();
  const provided = (data?.provided ?? []).filter((p) => !needle || `${p.repo_name} ${p.name} ${p.consumers.map((c) => `${c.repo_name} ${c.name}`).join(" ")} ${p.features.join(" ")}`.toLowerCase().includes(needle));
  if (!data) return error ? <ErrorNote message={error} /> : <Spinner label="Loading…" />;
  return (
    <div className="space-y-4">
      <section className="panel">
        <div className="panel-head"><span>Provided interfaces and who consumes them · {provided.length}</span></div>
        <div className="border-b border-line p-3"><input className="field max-w-md" placeholder="Search interface, repository, consumer or feature…" value={q} onChange={(e) => setQ(e.target.value)} /></div>
        <table className="w-full">
          <thead className="table-head"><tr><th>Provider</th><th>Interface</th><th>Type</th><th>Consumers</th><th>Feature</th></tr></thead>
          <tbody>
            {provided.map((p) => (
              <tr key={`${p.repo_id}:${p.key}`} className="table-row">
                <td className="font-mono text-code">{p.repo_name}</td>
                <td className="text-left">
                  <button type="button" className="link font-mono text-code text-left flex items-center max-w-full" onClick={() => p.path && setCode({ analysisId: p.analysis_id || analysisOf(p.repo_id), path: p.path, line: p.line, title: p.repo_name })}>
                    <EndpointLabel text={p.name} />
                  </button>
                  <div className="font-mono text-micro text-faint text-left">{p.item_kind}</div>
                </td>
                <td><KindChip kind={p.kind} /></td>
                <td>
                  <div className="flex flex-wrap gap-1">
                    {p.consumers.map((c, i) => <span key={i} className="chip border-line text-muted" title={`${c.name} · ${c.path}:${c.line} · ${c.status}`}>{c.repo_name}</span>)}
                  </div>
                  <div className="mt-0.5 truncate font-mono text-micro text-faint">{p.consumers.map((c) => c.name).slice(0, 3).join(" · ")}</div>
                </td>
                <td className="text-body-sm text-muted">{p.features.join(", ")}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {!provided.length && <Empty icon="lan" title="No cross-repository interfaces">Accepted or confirmed links appear here.</Empty>}
      </section>
      <section className="panel">
        <div className="panel-head"><span>External systems · {data.external.length}</span></div>
        <table className="w-full">
          <thead className="table-head"><tr><th>System</th><th>Type</th><th>Used by</th><th className="text-right">References</th></tr></thead>
          <tbody>
            {data.external.map((e) => (
              <tr key={`${e.type}:${e.name}`} className="table-row">
                <td className="font-mono text-code">{e.name}</td>
                <td className="text-body-sm text-muted">{e.type}</td>
                <td className="text-body-sm">{[...new Set(e.users.map((u) => u.repo_name))].join(", ")}</td>
                <td className="text-right font-mono tnum">{e.users.length}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
      {code && code.analysisId && <CodeModal {...code} onClose={() => setCode(null)} />}
    </div>
  );
}

function JourneysTab({ landscape }: { landscape: Landscape }) {
  const [entries, setEntries] = useState<EntryPoint[] | null>(null);
  const [repo, setRepo] = useState("");
  const [kind, setKind] = useState("screen");
  const [q, setQ] = useState("");
  const [journey, setJourney] = useState<Journey | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");
  const [mockupScreen, setMockupScreen] = useState<{
    analysisId: string;
    itemId?: string;
    itemKey?: string;
    screenName: string;
    repoName?: string;
  } | null>(null);
  const [code, setCode] = useState<{ analysisId: string; path: string; line: number; title: string } | null>(null);

  const analysisOf = (repoId: string) => getRepoAnalysisId(landscape, repoId);

  useEffect(() => {
    api<EntryPoint[]>(`/api/landscapes/${landscape.landscape_id}/entry-points`).then(setEntries).catch((err) => setError(errorText(err)));
  }, [landscape]);
  const needle = q.trim().toLowerCase();
  const shown = (entries ?? []).filter((e) => (!repo || e.repo_id === repo) && (!kind || e.kind === kind) && (!needle || `${e.name} ${e.path}`.toLowerCase().includes(needle)));

  async function open(e: EntryPoint) {
    setLoading(true);
    setError("");
    try {
      setJourney(await api<Journey>(`/api/landscapes/${landscape.landscape_id}/journey?repo_id=${e.repo_id}&key=${encodeURIComponent(e.item_key)}`));
    } catch (err) {
      setError(errorText(err));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="flex h-full min-h-0 flex-1 flex-col">
      <SplitPane
        direction="horizontal"
        primary="first"
        initialSize={360}
        minSize={260}
        maxSize={640}
        firstTitle="Starting Points"
        secondTitle={journey ? `Journey · ${journey.title}` : "Journey Flow"}
        first={
          <div className="flex h-full min-h-0 flex-col overflow-hidden">
            <div className="panel-head shrink-0 pr-9">
              <span>Start from</span>
              <span>{shown.length}</span>
            </div>
            <div className="space-y-2 border-b border-line bg-surface p-3 shrink-0">
              <Select className="field" value={repo} onChange={(e) => setRepo(e.target.value)} aria-label="Repository">
                <option value="">All repositories</option>
                {landscape.repos.map((r) => (
                  <option key={r.repo_id} value={r.repo_id}>{r.name}</option>
                ))}
              </Select>
              <div className="flex gap-1">
                {[["screen", "Screens"], ["endpoint", "Endpoints"], ["job", "Jobs"], ["listener", "Listeners"]].map(([k, l]) => (
                  <button
                    key={k}
                    type="button"
                    className={`btn h-7 flex-1 justify-center ${kind === k ? "border-primary bg-primary/15 text-primary" : ""}`}
                    onClick={() => setKind(kind === k ? "" : k)}
                  >
                    {l}
                  </button>
                ))}
              </div>
              <input className="field" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
            </div>
            <div className="min-h-0 flex-1 overflow-auto">
              {!entries && <div className="p-3"><Spinner /></div>}
              {shown.slice(0, 300).map((e) => {
                const isSelected = journey?.start.key === e.item_key && journey.start.repo_id === e.repo_id;
                return (
                  <div
                    key={`${e.repo_id}:${e.item_key}`}
                    className={`group flex items-center justify-between border-b border-line/60 px-3 py-1.5 hover:bg-elevated ${
                      isSelected ? "bg-primary/10" : ""
                    }`}
                  >
                    <button
                      type="button"
                      onClick={() => void open(e)}
                      className="min-w-0 flex-1 text-left"
                    >
                      <div className="truncate font-mono text-code">{e.name}</div>
                      <div className="truncate text-micro text-faint">{e.repo_name} · {e.kind}</div>
                    </button>
                    {e.kind === "screen" && (
                      <button
                        type="button"
                        className="btn h-6 shrink-0 ml-2 px-2 text-micro border border-line bg-surface hover:bg-elevated hover:border-primary/50 text-muted hover:text-ink flex items-center gap-1 shadow-xs transition-colors"
                        title={`View mockup for ${e.name}`}
                        onClick={(evt) => {
                          evt.stopPropagation();
                          setMockupScreen({
                            analysisId: e.analysis_id || analysisOf(e.repo_id),
                            itemId: e.item_id,
                            itemKey: e.item_key,
                            screenName: e.name,
                            repoName: e.repo_name,
                          });
                        }}
                      >
                        <span className="material-symbols-outlined icon-sm text-primary">devices</span>
                        Mockup
                      </button>
                    )}
                  </div>
                );
              })}
            </div>
          </div>
        }
        second={
          <div className="flex h-full min-h-0 flex-col overflow-hidden">
            <ErrorNote message={error} />
            {loading && (
              <div className="flex flex-1 items-center justify-center p-6">
                <Spinner label="Tracing…" />
              </div>
            )}
            {!journey && !loading && (
              <div className="flex flex-1 items-center justify-center p-6">
                <Empty icon="route" title="Pick a starting point">
                  A screen, endpoint, job or listener; the path is followed through the repository and across every accepted link.
                </Empty>
              </div>
            )}
            {journey && !loading && (
              <div className="min-h-0 flex-1">
                <SplitPane
                  direction="vertical"
                  primary="first"
                  initialSize={380}
                  minSize={160}
                  maxSize={780}
                  firstTitle={`Journey Diagram · ${journey.title}`}
                  secondTitle={`Journey Steps · ${journey.title}`}
                  first={
                    <div className="relative flex h-full w-full flex-col overflow-hidden">
                      <div className="panel-head shrink-0 pr-9">
                        <div className="flex items-center gap-2">
                          <span>Sequence Diagram</span>
                          {journey.start.kind === "screen" && (
                            <button
                              type="button"
                              className="btn h-6 px-2 text-micro border border-primary/40 bg-primary/10 text-primary hover:bg-primary/20 flex items-center gap-1 font-medium"
                              onClick={() => {
                                setMockupScreen({
                                  analysisId: journey.start.analysis_id || analysisOf(journey.start.repo_id),
                                  itemId: journey.start.item_id,
                                  itemKey: journey.start.key,
                                  screenName: journey.start.name,
                                  repoName: landscape.repos.find((r) => r.repo_id === journey.start.repo_id)?.name || "",
                                });
                              }}
                            >
                              <span className="material-symbols-outlined icon-sm">devices</span>
                              View Mockup
                            </button>
                          )}
                        </div>
                        <span>{journey.participants.length} participants</span>
                      </div>
                      <div className="relative min-h-0 flex-1 overflow-auto p-4 grid-canvas">
                        <Mermaid source={journey.mermaid} />
                      </div>
                    </div>
                  }
                  second={
                    <div className="flex h-full min-h-0 flex-col overflow-hidden">
                      <div className="panel-head shrink-0 pr-9">
                        <span>Steps</span>
                        <span>{journey.steps.length} steps</span>
                      </div>
                      <div className="min-h-0 flex-1 overflow-auto">
                        <table className="w-full">
                          <thead className="table-head sticky top-0 z-10 bg-surface">
                            <tr>
                              <th>#</th>
                              <th>From</th>
                              <th>To</th>
                              <th>Type</th>
                              <th>Interface / steps</th>
                              <th>Via</th>
                              <th>Status</th>
                            </tr>
                          </thead>
                          <tbody>
                            {journey.steps.map((s, i) => (
                              <tr key={i} className="table-row">
                                <td className="font-mono text-micro text-faint">{i + 1}</td>
                                <td className="font-mono text-code">{s.from}</td>
                                <td className="font-mono text-code">{s.kind === "internal" ? "" : s.to}</td>
                                <td>
                                  {s.kind === "internal" ? (
                                    <span className="chip border-line text-faint">inside</span>
                                  ) : s.kind === "table" ? (
                                    <span className="chip border-layer-data/50 text-layer-data">table</span>
                                  ) : (
                                    <KindChip kind={s.kind} />
                                  )}
                                </td>
                                <td className="max-w-lg font-mono text-micro">
                                  {s.label}
                                  {s.target && s.kind !== "internal" ? <span className="text-faint"> → {s.target}</span> : null}
                                </td>
                                <td className="font-mono text-micro text-muted">{s.via}</td>
                                <td>{s.status !== "internal" && <StatusPill status={s.status} />}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                        {!journey.steps.some((s) => s.kind !== "internal" && s.kind !== "table") && (
                          <p className="p-3 text-body-sm text-muted">
                            This starting point does not call another repository or external system.
                          </p>
                        )}
                      </div>
                    </div>
                  }
                />
              </div>
            )}
          </div>
        }
      />
      {mockupScreen && mockupScreen.analysisId && (
        <ScreenMockupModal
          analysisId={mockupScreen.analysisId}
          itemId={mockupScreen.itemId}
          itemKey={mockupScreen.itemKey}
          screenName={mockupScreen.screenName}
          repoName={mockupScreen.repoName}
          onClose={() => setMockupScreen(null)}
          onOpenFile={(path, line) => {
            setCode({
              analysisId: mockupScreen.analysisId,
              path,
              line: line || 1,
              title: mockupScreen.repoName || "Source",
            });
          }}
        />
      )}
      {code && code.analysisId && <CodeModal {...code} onClose={() => setCode(null)} />}
    </div>
  );
}

function LinksTab({ landscape, canWrite, initial, onChanged }: { landscape: Landscape; canWrite: boolean; initial: { repo: string; kind: string }; onChanged: () => void }) {
  const [kind, setKind] = useState(initial.kind);
  const [status, setStatus] = useState("");
  const [repo, setRepo] = useState(initial.repo);
  const [q, setQ] = useState("");
  const [rows, setRows] = useState<LandscapeLink[] | null>(null);
  const [open, setOpen] = useState<ReviewGroup | null>(null);
  const [error, setError] = useState("");
  const load = useCallback(() => {
    const qs = new URLSearchParams({ kind, status, repo_id: repo, q });
    api<{ items: LandscapeLink[] }>(`/api/landscapes/${landscape.landscape_id}/links?${qs}`).then((d) => setRows(d.items)).catch((err) => setError(errorText(err)));
  }, [landscape.landscape_id, kind, status, repo, q]);
  useEffect(() => {
    const timer = setTimeout(load, 250);
    return () => clearTimeout(timer);
  }, [load]);

  async function openGroup(link: LandscapeLink) {
    const queue = await api<ReviewQueue>(`/api/landscapes/${landscape.landscape_id}/review`);
    const all = [...queue.pending, ...queue.decided, ...queue.unresolved];
    const found = all.find((g) => g.group_key === link.group_key);
    if (found) {
      setOpen(found);
      return;
    }
    const group = await api<{ items: LandscapeLink[] }>(`/api/landscapes/${landscape.landscape_id}/links?q=&limit=10000`);
    const candidates = group.items.filter((l) => l.group_key === link.group_key);
    setOpen({ group_key: link.group_key, kind: link.kind, source_repo_id: link.source_repo_id, source_repo_name: link.source_repo_name, source_name: link.source_name, source_kind: link.source_kind,
      source_path: link.source_path, source_line: link.source_line, label: link.label, candidates, features: link.features, ambiguous: link.ambiguous });
  }

  return (
    <section className="panel">
      <div className="flex flex-wrap items-end gap-2 border-b border-line p-3">
        <Select className="field w-40" value={kind} onChange={(e) => setKind(e.target.value)} aria-label="Type">
          <option value="">All types</option>
          {ALL_KINDS.map((k) => <option key={k} value={k}>{KIND_META[k].label}</option>)}
        </Select>
        <Select className="field w-40" value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Status">
          <option value="">Any status</option>
          <option value="active">Accepted + external</option>
          <option value="auto">Auto-accepted</option>
          <option value="confirmed">Confirmed</option>
          <option value="review">To review</option>
          <option value="rejected">Rejected</option>
          <option value="candidate">Alternatives</option>
          <option value="unresolved">Unresolved</option>
        </Select>
        <Select className="field w-56" value={repo} onChange={(e) => setRepo(e.target.value)} aria-label="Repository">
          <option value="">All repositories</option>
          {landscape.repos.map((r) => <option key={r.repo_id} value={r.repo_id}>{r.name}</option>)}
        </Select>
        <input className="field max-w-sm flex-1" placeholder="Search…" value={q} onChange={(e) => setQ(e.target.value)} />
        <span className="ml-auto font-mono text-micro text-faint">{rows?.length ?? 0} links</span>
      </div>
      <ErrorNote message={error} />
      <table className="w-full">
        <thead className="table-head"><tr><th>Type</th><th>From</th><th>To</th><th>Confidence</th><th>Status</th><th>Feature</th><th /></tr></thead>
        <tbody>
          {(rows ?? []).map((l) => (
            <tr key={l.link_id} className="table-row">
              <td><KindChip kind={l.kind} /></td>
              <td className="max-w-sm">
                <div className="truncate font-mono text-code"><span className="text-muted">{l.source_repo_name} ·</span> {l.source_name}</div>
                <div className="truncate font-mono text-micro text-faint">{l.source_path}{l.source_line ? `:${l.source_line}` : ""}</div>
              </td>
              <td className="max-w-sm">
                <div className="truncate font-mono text-code"><span className="text-muted">{l.target_repo_name || l.external_type} ·</span> {l.target_name || l.external_name}</div>
                <div className="truncate font-mono text-micro text-faint">{l.label}</div>
              </td>
              <td><ConfidencePill link={l} /></td>
              <td><StatusPill status={l.status} /></td>
              <td className="text-body-sm text-muted">{l.features.slice(0, 2).join(", ")}</td>
              <td className="text-right">{l.kind !== "external" && <button type="button" className="btn h-6" onClick={() => void openGroup(l)}>{canWrite ? "Review…" : "View"}</button>}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {rows && !rows.length && <Empty icon="link_off" title="No links match" />}
      {open && <ReviewDialog landscapeId={landscape.landscape_id} group={open} canWrite={canWrite} onClose={() => setOpen(null)} onDecided={() => { setOpen(null); load(); onChanged(); }} />}
    </section>
  );
}

export function LandscapePage() {
  const { landscapeId = "" } = useParams();
  const { user } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const tab = (params.get("tab") as Tab) || "map";
  const [landscape, setLandscape] = useState<Landscape | null>(null);
  const [editing, setEditing] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");
  const [linkFilter, setLinkFilter] = useState({ repo: "", kind: "" });
  const canWrite = can(user, "insights", "write");

  const load = useCallback(() => {
    api<Landscape>(`/api/landscapes/${landscapeId}`).then(setLandscape).catch((err) => setError(errorText(err)));
  }, [landscapeId]);
  useEffect(load, [load]);
  const setTab = (t: Tab) => setParams({ tab: t });

  async function action(key: string, fn: () => Promise<void>) {
    setBusy(key);
    setError("");
    try {
      await fn();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy("");
    }
  }

  if (!landscape) return <div className="p-6">{error ? <ErrorNote message={error} /> : <Spinner label="Loading landscape…" />}</div>;
  const stats = landscape.stats;
  const file = `LANDSCAPE-${landscape.name.replace(/[^A-Za-z0-9_-]+/g, "-").slice(0, 40)}`;
  return (
    <div className="flex h-full flex-col">
      <PageHeader
        icon="hub"
        title={landscape.name}
        subtitle={
          <span className="flex flex-wrap items-center gap-x-3 gap-y-1">
            {landscape.description && <span>{landscape.description}</span>}
            <span className="font-mono text-code text-faint">{landscape.repos.map((r) => r.name).join(" · ")}</span>
            <span className="font-mono text-micro text-faint">linked {timeAgo(landscape.computed_at)} · auto-accept {landscape.auto_accept === "high" ? "high only" : "high + medium"}</span>
          </span>
        }
        right={
          <>
            <span className="chip border-ok/50 text-ok">{stats.links ?? 0} links</span>
            <span className="chip border-line text-muted">{stats.external ?? 0} external</span>
            {(stats.review ?? 0) > 0 && <button type="button" className="chip border-warning/50 text-warning" onClick={() => setTab("review")}>{stats.review} to review</button>}
            {canWrite && (
              <button type="button" className="btn" disabled={!!busy} title="Relink with the latest analyses" onClick={() => void action("relink", async () => setLandscape(await post<Landscape>(`/api/landscapes/${landscapeId}/recompute`)))}>
                <span className="material-symbols-outlined icon-sm">refresh</span>{busy === "relink" ? "Linking…" : "Relink"}
              </button>
            )}
            <button type="button" className="btn" disabled={!!busy} onClick={() => void action("md", () => downloadFile(`/api/landscapes/${landscapeId}/report.md`, `${file}.md`))}><span className="material-symbols-outlined icon-sm">description</span>MD</button>
            <button type="button" className="btn" disabled={!!busy} onClick={() => void action("pdf", () => downloadFile(`/api/landscapes/${landscapeId}/report.pdf`, `${file}.pdf`))}><span className="material-symbols-outlined icon-sm">picture_as_pdf</span>PDF</button>
            <button type="button" className="btn" disabled={!!busy} onClick={() => void action("xlsx", () => downloadFile(`/api/landscapes/${landscapeId}/export.xlsx`, `${file}.xlsx`))}><span className="material-symbols-outlined icon-sm">table_view</span>Excel</button>
            {canWrite && <button type="button" className="btn" onClick={() => setEditing(true)}><span className="material-symbols-outlined icon-sm">edit</span>Edit</button>}
            {canWrite && <button type="button" className="btn btn-danger" title="Delete landscape" onClick={() => setDeleting(true)}><span className="material-symbols-outlined icon-sm">delete</span></button>}
          </>
        }
      />
      <nav className="flex overflow-x-auto border-b border-line bg-surface">
        {TABS.map(([key, icon, label]) => (
          <button key={key} type="button" className={`tab ${tab === key ? "tab-active" : ""}`} onClick={() => setTab(key)}>
            <span className="material-symbols-outlined icon-sm">{icon}</span>{label}
            {key === "review" && (stats.review ?? 0) > 0 && <span className="chip border-warning/50 text-warning">{stats.review}</span>}
          </button>
        ))}
      </nav>
      <div className="flex min-h-0 flex-1 flex-col overflow-hidden">
        {error && <div className="p-4 pb-0"><ErrorNote message={error} /></div>}
        {(stats.missing ?? []).length > 0 && (
          <div className="p-4 pb-0">
            <p className="border border-warning/40 bg-warning/10 px-3 py-2 text-body-md text-warning">
              Not analysed yet, so not linked: {stats.missing?.join(", ")}. <Link className="link" to="/repositories">Analyse them</Link>.
            </p>
          </div>
        )}
        {tab === "map" && <MapTab landscape={landscape} onOpenLinks={(repo, kind) => { setLinkFilter({ repo, kind }); setTab("links"); }} />}
        {tab === "review" && <div className="flex-1 overflow-auto p-4 space-y-4"><ReviewTab landscape={landscape} canWrite={canWrite} onChanged={load} /></div>}
        {tab === "interfaces" && <div className="flex-1 overflow-auto p-4 space-y-4"><InterfacesTab landscape={landscape} /></div>}
        {tab === "journeys" && <JourneysTab landscape={landscape} />}
        {tab === "links" && <div className="flex-1 overflow-auto p-4 space-y-4"><LinksTab key={`${linkFilter.repo}:${linkFilter.kind}`} landscape={landscape} canWrite={canWrite} initial={linkFilter} onChanged={load} /></div>}
      </div>
      {editing && <LandscapeDialog landscape={landscape} onClose={() => setEditing(false)} onSaved={(l) => { setEditing(false); setLandscape(l); }} />}
      {deleting && (
        <ConfirmDialog title="Delete landscape" message={`Delete “${landscape.name}” and its review decisions? The repositories and analyses are kept.`} onClose={() => setDeleting(false)}
          onConfirm={async () => { await del(`/api/landscapes/${landscapeId}`); navigate("/landscapes"); }} />
      )}
    </div>
  );
}
