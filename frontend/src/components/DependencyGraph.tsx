import { useEffect, useMemo, useRef, useState } from "react";
import type { GraphEdge, GraphNode } from "../types";

const COLUMN: Record<string, number> = { ui: 0, api: 1, service: 2, data: 3, shared: 4 };
export const EDGE_KINDS = ["IMPORTS", "CALLS", "INJECTS", "EXTENDS", "IMPLEMENTS", "INCLUDES"] as const;
export const EDGE_VAR: Record<string, string> = {
  IMPORTS: "edge-imports",
  CALLS: "edge-calls",
  INJECTS: "edge-injects",
  EXTENDS: "edge-extends",
  IMPLEMENTS: "edge-implements",
  INCLUDES: "edge-includes",
};
const LAYER_VAR: Record<string, string> = { api: "layer-api", service: "layer-service", data: "layer-data", ui: "layer-ui", shared: "layer-shared" };

type Point = { x: number; y: number; vx: number; vy: number };

function basename(path: string) {
  return path.split("/").pop() ?? path;
}

/** Force-directed, layer-banded dependency graph (columns: ui → api → service → data → shared). */
export function DependencyGraph({
  nodes,
  edges,
  selected,
  onSelect,
  kinds,
}: {
  nodes: GraphNode[];
  edges: GraphEdge[];
  selected: string | null;
  onSelect: (path: string) => void;
  kinds: Set<string>;
}) {
  const [positions, setPositions] = useState<Record<string, Point>>({});
  const [view, setView] = useState({ x: 0, y: 0, k: 1 });
  const [hover, setHover] = useState<string | null>(null);
  const [layoutSeed, setLayoutSeed] = useState(0);
  const drag = useRef<{ x: number; y: number; vx: number; vy: number } | null>(null);
  const nodeDrag = useRef<{ path: string; startX: number; startY: number; origX: number; origY: number; moved: boolean } | null>(null);
  const svg = useRef<SVGSVGElement>(null);
  const box = useRef<HTMLDivElement>(null);
  const [size, setSize] = useState({ width: 900, height: 700 });
  const WIDTH = size.width;
  const HEIGHT = size.height;

  useEffect(() => {
    if (!box.current) return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      if (width > 50 && height > 50) setSize((s) => (Math.abs(s.width - width) > 20 || Math.abs(s.height - height) > 20 ? { width: Math.round(width), height: Math.round(height) } : s));
    });
    observer.observe(box.current);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const el = svg.current;
    if (!el) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = el.getBoundingClientRect();
      const mx = event.clientX - rect.left;
      const my = event.clientY - rect.top;
      const factor = event.deltaY > 0 ? 0.9 : 1.1;
      setView((v) => {
        const nextK = Math.max(0.25, Math.min(4, Number((v.k * factor).toFixed(3))));
        const ratio = nextK / v.k;
        return {
          x: mx - (mx - v.x) * ratio,
          y: my - (my - v.y) * ratio,
          k: nextK,
        };
      });
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, []);

  const visibleEdges = useMemo(() => edges.filter((e) => kinds.has(e.kind)), [edges, kinds]);
  const maxRank = useMemo(() => Math.max(0.0001, ...nodes.map((n) => n.pagerank)), [nodes]);

  // layout simulation
  useEffect(() => {
    const pos: Record<string, Point> = {};
    const columns = Object.keys(COLUMN).length;
    nodes.forEach((node, index) => {
      const column = COLUMN[node.layer] ?? 4;
      pos[node.path] = {
        x: ((column + 0.5) * WIDTH) / columns + (Math.random() - 0.5) * 90,
        y: 60 + ((index * 97) % Math.max(120, HEIGHT - 120)) + Math.random() * 20,
        vx: 0,
        vy: 0,
      };
    });
    const list = nodes.map((n) => n.path);
    const links = edges.filter((e) => pos[e.source_path] && pos[e.target_path]);
    let tick = 0;
    let frame = 0;
    const total = 220;
    const step = () => {
      for (let iteration = 0; iteration < 4 && tick < total; iteration++, tick++) {
        const alpha = 1 - tick / total;
        for (let i = 0; i < list.length; i++) {
          const a = pos[list[i]];
          for (let j = i + 1; j < list.length; j++) {
            const b = pos[list[j]];
            let dx = a.x - b.x;
            let dy = a.y - b.y;
            let distance = dx * dx + dy * dy;
            if (distance > 40000) continue;
            if (distance < 1) {
              dx = Math.random();
              dy = Math.random();
              distance = 1;
            }
            const force = (1400 / distance) * alpha;
            a.vx += dx * force * 0.05;
            a.vy += dy * force * 0.05;
            b.vx -= dx * force * 0.05;
            b.vy -= dy * force * 0.05;
          }
        }
        for (const link of links) {
          const a = pos[link.source_path];
          const b = pos[link.target_path];
          const dx = b.x - a.x;
          const dy = b.y - a.y;
          const distance = Math.sqrt(dx * dx + dy * dy) || 1;
          const force = ((distance - 110) / distance) * 0.02 * alpha;
          a.vx += dx * force;
          a.vy += dy * force;
          b.vx -= dx * force;
          b.vy -= dy * force;
        }
        for (const node of nodes) {
          const p = pos[node.path];
          const columnX = (((COLUMN[node.layer] ?? 4) + 0.5) * WIDTH) / columns;
          p.vx += (columnX - p.x) * 0.025;
          p.vy += (HEIGHT / 2 - p.y) * 0.002;
          p.vx *= 0.6;
          p.vy *= 0.6;
          p.x = Math.max(20, Math.min(WIDTH - 20, p.x + p.vx));
          p.y = Math.max(24, Math.min(HEIGHT - 20, p.y + p.vy));
        }
      }
      setPositions({ ...pos });
      if (tick < total) frame = requestAnimationFrame(step);
    };
    frame = requestAnimationFrame(step);
    return () => cancelAnimationFrame(frame);
  }, [nodes, edges, WIDTH, HEIGHT, layoutSeed]);

  const focus = hover ?? selected;
  const neighbours = useMemo(() => {
    const set = new Set<string>();
    if (!focus) return set;
    for (const edge of visibleEdges) {
      if (edge.source_path === focus) set.add(edge.target_path);
      if (edge.target_path === focus) set.add(edge.source_path);
    }
    return set;
  }, [focus, visibleEdges]);
  const labelled = useMemo(() => new Set([...nodes].sort((a, b) => b.pagerank - a.pagerank).slice(0, 40).map((n) => n.path)), [nodes]);

  const color = (name: string, alpha = 1) => `rgb(var(--${name}) / ${alpha})`;

  return (
    <div ref={box} className="relative h-full w-full grid-canvas overflow-hidden select-none">
      <svg
        ref={svg}
        className="h-full w-full cursor-grab active:cursor-grabbing"
        viewBox={`0 0 ${WIDTH} ${HEIGHT}`}
        onMouseDown={(event) => {
          if (event.button !== 0) return;
          drag.current = { x: event.clientX, y: event.clientY, vx: view.x, vy: view.y };
        }}
        onMouseMove={(event) => {
          if (nodeDrag.current) {
            const nd = nodeDrag.current;
            const dx = (event.clientX - nd.startX) / view.k;
            const dy = (event.clientY - nd.startY) / view.k;
            if (Math.abs(dx) > 2 || Math.abs(dy) > 2) nd.moved = true;
            setPositions((prev) => ({
              ...prev,
              [nd.path]: { ...(prev[nd.path] ?? { vx: 0, vy: 0 }), x: nd.origX + dx, y: nd.origY + dy, vx: 0, vy: 0 },
            }));
            return;
          }
          if (!drag.current || !svg.current) return;
          setView((v) => ({
            ...v,
            x: drag.current!.vx + (event.clientX - drag.current!.x),
            y: drag.current!.vy + (event.clientY - drag.current!.y),
          }));
        }}
        onMouseUp={() => {
          drag.current = null;
          nodeDrag.current = null;
        }}
        onMouseLeave={() => {
          drag.current = null;
          nodeDrag.current = null;
        }}
      >
        <defs>
          {EDGE_KINDS.map((kind) => (
            <marker key={kind} id={`arrow-${kind}`} viewBox="0 0 10 10" refX="9" refY="5" markerWidth="5" markerHeight="5" orient="auto-start-reverse">
              <path d="M 0 0 L 10 5 L 0 10 z" fill={color(EDGE_VAR[kind])} />
            </marker>
          ))}
        </defs>
        <g transform={`translate(${view.x} ${view.y}) scale(${view.k})`}>
          {Object.keys(COLUMN).map((layer) => (
            <text key={layer} x={((COLUMN[layer] + 0.5) * WIDTH) / 5} y={14} textAnchor="middle" className="font-mono" fontSize={11} fill={color(LAYER_VAR[layer], 0.7)}>
              {layer.toUpperCase()}
            </text>
          ))}
          {visibleEdges.map((edge, index) => {
            const a = positions[edge.source_path];
            const b = positions[edge.target_path];
            if (!a || !b) return null;
            const active = focus && (edge.source_path === focus || edge.target_path === focus);
            const dimmed = focus && !active;
            const dx = b.x - a.x;
            const dy = b.y - a.y;
            const length = Math.sqrt(dx * dx + dy * dy) || 1;
            const endX = b.x - (dx / length) * 10;
            const endY = b.y - (dy / length) * 10;
            return (
              <line
                key={index}
                x1={a.x}
                y1={a.y}
                x2={endX}
                y2={endY}
                stroke={color(EDGE_VAR[edge.kind] ?? "edge-imports", dimmed ? 0.08 : active ? 0.95 : 0.35)}
                strokeWidth={active ? 1.6 : 0.8}
                strokeDasharray={edge.kind === "INJECTS" ? "4 3" : edge.kind === "IMPLEMENTS" ? "2 2" : undefined}
                markerEnd={dimmed ? undefined : `url(#arrow-${edge.kind})`}
              />
            );
          })}
          {nodes.map((node) => {
            const p = positions[node.path];
            if (!p) return null;
            const radius = 4 + 10 * Math.sqrt(node.pagerank / maxRank);
            const isSelected = node.path === selected;
            const isNeighbour = neighbours.has(node.path);
            const dimmed = focus && !isSelected && !isNeighbour && node.path !== focus;
            const showLabel = isSelected || isNeighbour || node.path === hover || labelled.has(node.path);
            return (
              <g
                key={node.path}
                transform={`translate(${p.x} ${p.y})`}
                className="cursor-grab active:cursor-grabbing"
                opacity={dimmed ? 0.25 : 1}
                onMouseEnter={() => setHover(node.path)}
                onMouseLeave={() => setHover(null)}
                onMouseDown={(event) => {
                  event.stopPropagation();
                  nodeDrag.current = {
                    path: node.path,
                    startX: event.clientX,
                    startY: event.clientY,
                    origX: p.x,
                    origY: p.y,
                    moved: false,
                  };
                }}
                onClick={() => {
                  if (!nodeDrag.current?.moved) onSelect(node.path);
                }}
              >
                {node.scc_id !== null && <circle r={radius + 4} fill="none" stroke={color("critical")} strokeDasharray="3 2" strokeWidth={1.2} />}
                {isSelected && <circle r={radius + 7} fill="none" stroke={color("primary")} strokeWidth={1.5} />}
                <rect
                  x={-radius}
                  y={-radius}
                  width={radius * 2}
                  height={radius * 2}
                  rx={node.is_entry ? radius : 2}
                  fill={color(LAYER_VAR[node.layer] ?? "layer-shared", 0.22)}
                  stroke={color(LAYER_VAR[node.layer] ?? "layer-shared")}
                  strokeWidth={1.2}
                />
                {showLabel && (
                  <text x={radius + 4} y={3} fontSize={10} className="font-mono" fill="rgb(var(--ink))" style={{ paintOrder: "stroke", stroke: "rgb(var(--canvas))", strokeWidth: 3 }}>
                    {basename(node.path)}
                  </text>
                )}
                <title>{`${node.path}\n${node.layer} · ${node.role}\nfan-in ${node.fan_in} · fan-out ${node.fan_out} · PR ${node.pagerank}`}</title>
              </g>
            );
          })}
        </g>
      </svg>
      <div className="absolute right-2 top-2 z-10 flex items-center gap-1">
        <button
          type="button"
          className="icon-btn border border-line bg-surface"
          title="Zoom in"
          onClick={() => setView((v) => ({ ...v, k: Math.min(4, Number((v.k + 0.2).toFixed(2))) }))}
        >
          <span className="material-symbols-outlined">zoom_in</span>
        </button>
        <button
          type="button"
          className="icon-btn border border-line bg-surface"
          title="Zoom out"
          onClick={() => setView((v) => ({ ...v, k: Math.max(0.25, Number((v.k - 0.2).toFixed(2))) }))}
        >
          <span className="material-symbols-outlined">zoom_out</span>
        </button>
        <button
          type="button"
          className="icon-btn border border-line bg-surface"
          title="Re-align & fit diagram"
          onClick={() => {
            setView({ x: 0, y: 0, k: 1 });
            setLayoutSeed((s) => s + 1);
          }}
        >
          <span className="material-symbols-outlined">center_focus_strong</span>
        </button>
      </div>
      <div className="absolute bottom-2 left-2 flex gap-1">
        <button type="button" className="icon-btn border border-line bg-surface" title="Reset view" onClick={() => setView({ x: 0, y: 0, k: 1 })}>
          <span className="material-symbols-outlined">fit_screen</span>
        </button>
        <span className="border border-line bg-surface px-2 font-mono text-micro text-muted leading-6">
          Zoom {Math.round(view.k * 100)}% · {nodes.length} nodes · {visibleEdges.length} edges
        </span>
      </div>
    </div>
  );
}
