import { useEffect, useMemo, useState } from "react";
import { api, compact } from "../../api";
import { LAYER_DOT, LayerPill, RoleBadge, Select, Spinner, SplitPane } from "../../components/ui";
import type { Analysis, FileRow, Layer, TreeNode } from "../../types";

function TreeItem({ node, depth, layers, onOpen, open, toggle }: { node: TreeNode; depth: number; layers: Set<Layer>; onOpen: (path: string) => void; open: Set<string>; toggle: (path: string) => void }) {
  if (node.type === "file") {
    if (node.layer && !layers.has(node.layer)) return null;
    return (
      <button type="button" className="flex w-full items-center gap-1.5 py-0.5 pr-2 text-left hover:bg-elevated" style={{ paddingLeft: depth * 14 + 6 }} onClick={() => onOpen(node.path)}>
        <span className={`h-1.5 w-1.5 shrink-0 ${LAYER_DOT[node.layer ?? "shared"]}`} />
        <span className={`flex-1 truncate font-mono text-code ${node.is_test ? "text-faint" : ""}`}>{node.name}</span>
        {node.scc_id !== null && node.scc_id !== undefined && <span className="chip border-critical/50 text-critical">scc</span>}
        <span className="font-mono text-micro text-faint tnum">{node.loc}</span>
      </button>
    );
  }
  const isOpen = open.has(node.path);
  const visible = Object.entries(node.layers ?? {}).some(([layer, count]) => count && layers.has(layer as Layer));
  if (!visible && depth > 0) return null;
  return (
    <div>
      {depth > 0 && (
        <button type="button" className="flex w-full items-center gap-1 py-0.5 pr-2 text-left hover:bg-elevated" style={{ paddingLeft: depth * 14 - 8 }} onClick={() => toggle(node.path)}>
          <span className="material-symbols-outlined icon-sm text-faint">{isOpen ? "expand_more" : "chevron_right"}</span>
          <span className="material-symbols-outlined icon-sm text-primary">{isOpen ? "folder_open" : "folder"}</span>
          <span className="flex-1 truncate font-mono text-code">{node.name}</span>
          <span className="flex h-1.5 w-16 overflow-hidden bg-elevated">
            {Object.entries(node.layers ?? {}).map(([layer, count]) => (
              <span key={layer} className={LAYER_DOT[layer]} style={{ width: `${(100 * count) / (node.files || 1)}%` }} />
            ))}
          </span>
          <span className="w-12 text-right font-mono text-micro text-faint tnum">{node.files}</span>
        </button>
      )}
      {(isOpen || depth === 0) && node.children?.map((child) => <TreeItem key={child.path || child.name} node={child} depth={depth + 1} layers={layers} onOpen={onOpen} open={open} toggle={toggle} />)}
    </div>
  );
}

export function TopologyTab({ analysis, layers, onOpenFile }: { analysis: Analysis; layers: Set<Layer>; onOpenFile: (path: string) => void }) {
  const [tree, setTree] = useState<TreeNode | null>(null);
  const [open, setOpen] = useState<Set<string>>(new Set());
  const [files, setFiles] = useState<{ total: number; items: FileRow[] }>({ total: 0, items: [] });
  const [q, setQ] = useState("");
  const [role, setRole] = useState("");
  const [sort, setSort] = useState("pagerank");
  const [order, setOrder] = useState("desc");
  const [tests, setTests] = useState(false);
  const [page, setPage] = useState(0);
  const roles = useMemo(() => Object.keys(analysis.stats.roles ?? {}), [analysis]);
  const pageSize = 100;

  useEffect(() => {
    api<TreeNode>(`/api/analyses/${analysis.analysis_id}/tree`).then((data) => {
      setTree(data);
      setOpen(new Set((data.children ?? []).filter((c) => c.type === "dir").slice(0, 4).map((c) => c.path)));
    });
  }, [analysis.analysis_id]);

  useEffect(() => {
    const layerList = layers.size ? [...layers].join(",") : "__none__";
    const timer = setTimeout(() => {
      api<{ total: number; items: FileRow[] }>(
        `/api/analyses/${analysis.analysis_id}/files?q=${encodeURIComponent(q)}&layer=${layerList}&role=${encodeURIComponent(role)}&sort=${sort}&order=${order}&include_tests=${tests}&limit=${pageSize}&offset=${page * pageSize}`,
      ).then(setFiles);
    }, 150);
    return () => clearTimeout(timer);
  }, [analysis.analysis_id, q, role, sort, order, tests, page, layers]);

  const header = (key: string, label: string, right = false) => (
    <th className={`cursor-pointer select-none ${right ? "text-right" : ""}`} onClick={() => { if (sort === key) setOrder((o) => (o === "desc" ? "asc" : "desc")); else { setSort(key); setOrder("desc"); } }}>
      {label} {sort === key && (order === "desc" ? "↓" : "↑")}
    </th>
  );

  return (
    <SplitPane
      direction="horizontal"
      primary="first"
      initialSize={360}
      minSize={200}
      maxSize={720}
      firstTitle="Directory Tree"
      secondTitle="Files & Metrics Table"
      first={
        <div className="flex h-full min-h-0 flex-col">
          <div className="panel-head pr-9"><span>Directory tree</span><span>{tree?.files ?? 0} files</span></div>
          <div className="flex-1 overflow-auto py-1">
            {tree ? <TreeItem node={tree} depth={0} layers={layers} onOpen={onOpenFile} open={open} toggle={(path) => setOpen((s) => { const next = new Set(s); if (next.has(path)) next.delete(path); else next.add(path); return next; })} /> : <div className="p-3"><Spinner /></div>}
          </div>
        </div>
      }
      second={
        <div className="flex h-full min-h-0 flex-col">
          <div className="flex flex-wrap items-center gap-2 border-b border-line bg-surface px-3 pr-9 py-1.5">
            <input className="field h-7 w-64" placeholder="Filter path…" value={q} onChange={(e) => { setQ(e.target.value); setPage(0); }} />
            <Select className="field h-7 w-60" value={role} onChange={(e) => { setRole(e.target.value); setPage(0); }}>
              <option value="">All roles</option>
              {roles.map((r) => <option key={r} value={r}>{r} ({analysis.stats.roles[r]})</option>)}
            </Select>
            <label className="flex items-center gap-1 font-mono text-micro text-muted"><input type="checkbox" checked={tests} onChange={(e) => setTests(e.target.checked)} /> include tests</label>
            <span className="ml-auto font-mono text-micro text-faint">{files.total} files</span>
          </div>
          <div className="min-h-0 flex-1 overflow-auto">
            <table className="w-full">
              <thead className="table-head"><tr>{header("path", "File")}{header("layer", "Layer")}{header("role", "Role")}{header("loc", "LOC", true)}{header("complexity", "McCabe", true)}{header("fan_in", "In", true)}{header("fan_out", "Out", true)}{header("pagerank", "PageRank", true)}</tr></thead>
              <tbody>
                {files.items.map((f) => (
                  <tr key={f.path} className="table-row cursor-pointer" onClick={() => onOpenFile(f.path)}>
                    <td className="max-w-[420px]">
                      <div className="truncate font-mono text-code">{f.path}</div>
                      <div className="flex gap-2">{f.concepts.slice(0, 4).map((c) => <span key={c} className="font-mono text-micro text-faint">{c}</span>)}</div>
                    </td>
                    <td><LayerPill layer={f.layer} /></td>
                    <td><RoleBadge role={f.role} /></td>
                    <td className="text-right font-mono tnum">{compact(f.code_lines)}</td>
                    <td className={`text-right font-mono tnum ${f.max_function_complexity > 15 ? "text-warning" : ""}`}>{f.complexity}</td>
                    <td className="text-right font-mono tnum">{f.fan_in}</td>
                    <td className="text-right font-mono tnum">{f.fan_out}</td>
                    <td className="text-right font-mono text-primary tnum">{f.pagerank.toFixed(4)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="flex items-center justify-end gap-2 border-t border-line px-3 py-1 font-mono text-micro">
            <button type="button" className="btn h-6" disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Prev</button>
            <span className="text-muted">{page * pageSize + 1}–{Math.min(files.total, (page + 1) * pageSize)} of {files.total}</span>
            <button type="button" className="btn h-6" disabled={(page + 1) * pageSize >= files.total} onClick={() => setPage((p) => p + 1)}>Next</button>
          </div>
        </div>
      }
    />
  );
}
