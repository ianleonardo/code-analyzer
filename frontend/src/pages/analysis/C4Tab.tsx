import { useMemo, useState } from "react";
import { Mermaid } from "../../components/Mermaid";
import { Select, SplitPane } from "../../components/ui";
import { LAYERS, type Analysis, type Layer } from "../../types";
import type { C4Level } from "../AnalysisPage";

const LEVELS: { key: C4Level; label: string; help: string }[] = [
  { key: "context", label: "L1 Context", help: "The system, its users and the external systems it talks to." },
  { key: "container", label: "L2 Container", help: "Deployable units and workspace packages, with data stores and cross-container dependencies." },
  { key: "component", label: "L3 Component", help: "Modules inside a container (top folders), with aggregated dependency edges." },
  { key: "dynamic", label: "L4 Dynamic", help: "The dominant request path from an entry point down through the layers." },
];

function filterC4ByLayers(raw: string, layers: Set<Layer>): string {
  if (!raw || layers.size === LAYERS.length) return raw;
  const keptIds = new Set<string>(["user"]);
  const out: string[] = [];
  for (const line of raw.split("\n")) {
    const compMatch = line.match(/^\s*Component\(\s*([^,\s]+)\s*,\s*"[^"]*"\s*,\s*"([^"]*)"/);
    if (compMatch) {
      const [, id, layer] = compMatch;
      if (layers.has(layer as Layer)) {
        keptIds.add(id);
        out.push(line);
      }
      continue;
    }
    const relMatch = line.match(/^\s*Rel\(\s*([^,\s]+)\s*,\s*([^,\s]+)/);
    if (relMatch) {
      const [, a, b] = relMatch;
      if (keptIds.has(a) && keptIds.has(b)) out.push(line);
      continue;
    }
    out.push(line);
  }
  return out.join("\n");
}

function filterLayerFlow(raw: string, layers: Set<Layer>): string {
  if (!raw || layers.size === LAYERS.length) return raw;
  const out: string[] = [];
  for (const line of raw.split("\n")) {
    const nodeMatch = line.match(/^\s*(api|service|data|ui|shared)\[/);
    if (nodeMatch) {
      if (layers.has(nodeMatch[1] as Layer)) out.push(line);
      continue;
    }
    const edgeMatch = line.match(/^\s*(api|service|data|ui|shared)\s*-->\|[^|]*\|\s*(api|service|data|ui|shared)/);
    if (edgeMatch) {
      if (layers.has(edgeMatch[1] as Layer) && layers.has(edgeMatch[2] as Layer)) out.push(line);
      continue;
    }
    const clsMatch = line.match(/^\s*class\s+(api|service|data|ui|shared)\s+/);
    if (clsMatch) {
      if (layers.has(clsMatch[1] as Layer)) out.push(line);
      continue;
    }
    out.push(line);
  }
  return out.join("\n");
}

export function C4Tab({ analysis, level, layers, onLevel }: { analysis: Analysis; level: C4Level; layers: Set<Layer>; onLevel: (level: C4Level) => void }) {
  const names = Object.keys(analysis.c4.components ?? {});
  const [container, setContainer] = useState(names[0] ?? "");
  const rawSource =
    level === "context" ? analysis.c4.context : level === "container" ? analysis.c4.container : level === "component" ? analysis.c4.components?.[container] ?? "" : analysis.c4.dynamic;
  const source = useMemo(
    () => (level === "component" || level === "dynamic" ? filterC4ByLayers(rawSource, layers) : rawSource),
    [rawSource, level, layers],
  );
  const layerFlowSource = useMemo(() => filterLayerFlow(analysis.c4.layers, layers), [analysis.c4.layers, layers]);
  const meta = LEVELS.find((l) => l.key === level)!;
  return (
    <div className="flex h-full flex-col">
      <div className="flex flex-wrap items-center gap-3 border-b border-line bg-surface px-3 py-1.5">
        <span className="label-micro">Model view:</span>
        <div className="flex border border-line">
          {LEVELS.map((l) => (
            <button key={l.key} type="button" className={`h-7 px-3 font-mono text-code ${level === l.key ? "bg-primary/10 text-primary" : "text-muted hover:text-ink"}`} onClick={() => onLevel(l.key)}>
              {l.label}
            </button>
          ))}
        </div>
        {level === "component" && names.length > 1 && (
          <Select className="field h-7 w-56" value={container} onChange={(e) => setContainer(e.target.value)}>
            {names.map((n) => <option key={n} value={n}>{n}</option>)}
          </Select>
        )}
        <span className="text-body-sm text-muted">{meta.help}</span>
        {layers.size < LAYERS.length && (
          <span className="chip ml-auto border-primary/50 bg-primary/10 text-primary">{layers.size}/{LAYERS.length} layers active</span>
        )}
      </div>
      <div className="min-h-0 flex-1">
        <SplitPane
          direction="vertical"
          primary="second"
          initialSize={200}
          minSize={90}
          maxSize={560}
          firstTitle={`C4 Diagram · ${meta.label}`}
          secondTitle="Canonical Layer Flow"
          first={
            <div className="relative h-full w-full grid-canvas">
              <Mermaid source={source} />
            </div>
          }
          second={
            <div className="flex h-full flex-col">
              <div className="panel-head shrink-0 pr-9">
                <span>Layer flow · dependency edges between canonical layers</span>
                {layers.size < LAYERS.length && <span>filtered ({layers.size}/{LAYERS.length} layers)</span>}
              </div>
              <div className="min-h-0 flex-1 grid-canvas">
                <Mermaid source={layerFlowSource} />
              </div>
            </div>
          }
        />
      </div>
    </div>
  );
}
