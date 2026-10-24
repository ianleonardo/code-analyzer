import { useCallback, useEffect, useRef, useState } from "react";
import { useTheme } from "../theme";

let counter = 0;

/** A theme token as #rrggbb (Mermaid's classDef and C4 styles reject rgb() with commas). */
function css(name: string) {
  const raw = getComputedStyle(document.documentElement).getPropertyValue(`--${name}`).trim();
  const parts = raw.split(/\s+/).map(Number);
  if (parts.length === 3 && parts.every((n) => !Number.isNaN(n))) return `#${parts.map((n) => n.toString(16).padStart(2, "0")).join("")}`;
  return raw || "#888888";
}

const ELEMENT = /^\s*(Person(?:_Ext)?|System(?:Db|Queue)?(?:_Ext)?|Container(?:Db|Queue)?(?:_Ext)?|Component(?:Db|Queue)?(?:_Ext)?)\((\w+)\s*,\s*"[^"]*"(?:\s*,\s*"([^"]*)")?/;
const REL = /^\s*Rel\((\w+)\s*,\s*(\w+)/;
const LAYERS = ["api", "service", "data", "ui", "shared"];

/** Colours C4 elements with the workbench palette: components by canonical layer, stores and queues by kind. */
function styleC4(source: string): string {
  const styles: string[] = [];
  for (const line of source.split("\n")) {
    const element = ELEMENT.exec(line);
    if (element) {
      const [, kind, id, tech] = element;
      let bg = css("surface");
      let font = css("ink");
      let border = css("primary");
      if (kind.startsWith("Person")) {
        bg = css("primary");
        font = css("on-primary");
      } else if (kind.endsWith("_Ext")) {
        bg = css("elevated");
        border = css("line-strong");
        font = css("muted");
        if (kind.includes("Db")) border = css("layer-data");
        if (kind.includes("Queue")) border = css("layer-service");
      } else if (kind.includes("Db")) {
        bg = css("layer-data");
        font = "#0a0d12";
      } else if (kind.includes("Queue")) {
        bg = css("layer-service");
        font = "#ffffff";
      } else if (kind.startsWith("Component") && tech && LAYERS.includes(tech)) {
        bg = css("elevated");
        border = css(`layer-${tech}`);
      }
      styles.push(`  UpdateElementStyle(${id}, $bgColor="${bg}", $fontColor="${font}", $borderColor="${border}")`);
      continue;
    }
    const rel = REL.exec(line);
    if (rel) styles.push(`  UpdateRelStyle(${rel[1]}, ${rel[2]}, $textColor="${css("muted")}", $lineColor="${css("line-strong")}")`);
  }
  return `${source}\n${styles.join("\n")}`;
}

/**
 * Interactive Mermaid / C4 diagram viewport:
 * - Hand cursor drag-to-pan
 * - Mouse wheel scroll-to-zoom (towards cursor)
 * - Re-align / Fit & center button + orientation toggle for flowcharts
 */
export function Mermaid({ source, className = "" }: { source: string; className?: string }) {
  const viewportRef = useRef<HTMLDivElement>(null);
  const host = useRef<HTMLDivElement>(null);
  const { theme } = useTheme();
  const [error, setError] = useState("");
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 16, y: 16 });
  const [dragging, setDragging] = useState(false);
  const [flowDir, setFlowDir] = useState<"TB" | "LR" | null>(null);
  const dragStart = useRef<{ x: number; y: number; px: number; py: number } | null>(null);
  const [showSource, setShowSource] = useState(false);

  const isFlowchart = source.trimStart().startsWith("flowchart");

  const realign = useCallback(() => {
    const vp = viewportRef.current;
    const svgEl = host.current?.querySelector("svg");
    if (!vp || !svgEl) {
      setZoom(1);
      setPan({ x: 16, y: 16 });
      return;
    }
    const vpRect = vp.getBoundingClientRect();
    const bbox = svgEl.getBoundingClientRect();
    const naturalW = bbox.width / (zoom || 1);
    const naturalH = bbox.height / (zoom || 1);
    if (naturalW <= 0 || naturalH <= 0 || vpRect.width <= 0 || vpRect.height <= 0) {
      setZoom(1);
      setPan({ x: 16, y: 16 });
      return;
    }
    const pad = 32;
    const fitScale = Math.min(
      1.25,
      Math.max(0.25, Math.min((vpRect.width - pad) / naturalW, (vpRect.height - pad) / naturalH)),
    );
    const nextX = Math.round((vpRect.width - naturalW * fitScale) / 2);
    const nextY = Math.round(Math.max(12, (vpRect.height - naturalH * fitScale) / 2));
    setZoom(Number(fitScale.toFixed(2)));
    setPan({ x: nextX, y: nextY });
  }, [zoom]);

  useEffect(() => {
    let cancelled = false;
    setError("");
    if (!source.trim()) return;
    void (async () => {
      const mermaid = (await import("mermaid")).default;
      mermaid.initialize({
        startOnLoad: false,
        securityLevel: "strict",
        theme: "base",
        fontFamily: "JetBrains Mono, monospace",
        themeVariables: {
          background: css("canvas"),
          primaryColor: css("surface"),
          primaryTextColor: css("ink"),
          primaryBorderColor: css("primary"),
          lineColor: css("line-strong"),
          secondaryColor: css("elevated"),
          tertiaryColor: css("elevated"),
          textColor: css("ink"),
          fontSize: "12px",
          attributeBackgroundColorOdd: css("surface"),
          attributeBackgroundColorEven: css("elevated"),
          rowOdd: css("surface"),
          rowEven: css("elevated"),
          actorBkg: css("surface"),
          actorBorder: css("primary"),
          actorTextColor: css("ink"),
          actorLineColor: css("line-strong"),
          signalColor: css("muted"),
          signalTextColor: css("ink"),
          labelBoxBkgColor: css("elevated"),
          labelTextColor: css("ink"),
          noteBkgColor: css("elevated"),
          noteTextColor: css("ink"),
        },
        er: { useMaxWidth: false },
        sequence: { useMaxWidth: false, mirrorActors: false, wrap: true },
        c4: {
          useMaxWidth: false,
          diagramMarginX: 30,
          diagramMarginY: 10,
          c4ShapeMargin: 40,
          wrap: true,
          personFontSize: 12,
          systemFontSize: 12,
          containerFontSize: 12,
          componentFontSize: 12,
          messageFontSize: 11,
        },
        flowchart: { curve: "basis", htmlLabels: false, useMaxWidth: false },
      });
      const id = `mmd-${++counter}`;
      try {
        let text = source;
        if (flowDir && text.trimStart().startsWith("flowchart")) {
          text = text.replace(/^(\s*flowchart\s+)(TB|TD|BT|RL|LR)/, `$1${flowDir}`);
        }
        if (/^C4\w+/.test(text)) text = styleC4(text);
        if (text.startsWith("flowchart")) {
          text += `\n  classDef layer_api fill:${css("layer-api")},stroke:${css("layer-api")},color:#0a0d12\n  classDef layer_service fill:${css("layer-service")},stroke:${css("layer-service")},color:#ffffff\n  classDef layer_data fill:${css("layer-data")},stroke:${css("layer-data")},color:#0a0d12\n  classDef layer_ui fill:${css("layer-ui")},stroke:${css("layer-ui")},color:#ffffff\n  classDef layer_shared fill:${css("layer-shared")},stroke:${css("layer-shared")},color:#ffffff`;
        }
        const { svg } = await mermaid.render(id, text);
        if (!cancelled && host.current) {
          host.current.innerHTML = svg;
          requestAnimationFrame(() => {
            if (!cancelled) {
              const vp = viewportRef.current;
              const svgEl = host.current?.querySelector("svg");
              if (vp && svgEl) {
                const vpRect = vp.getBoundingClientRect();
                const svgRect = svgEl.getBoundingClientRect();
                const natW = svgRect.width / (zoom || 1);
                const natH = svgRect.height / (zoom || 1);
                if (natW > 0 && natH > 0 && vpRect.width > 0 && vpRect.height > 0) {
                  const fit = Math.min(1.15, Math.max(0.3, Math.min((vpRect.width - 32) / natW, (vpRect.height - 32) / natH)));
                  setZoom(Number(fit.toFixed(2)));
                  setPan({
                    x: Math.round((vpRect.width - natW * fit) / 2),
                    y: Math.round(Math.max(12, (vpRect.height - natH * fit) / 2)),
                  });
                }
              }
            }
          });
        }
      } catch (err) {
        document.getElementById(id)?.remove();
        document.getElementById(`d${id}`)?.remove();
        if (!cancelled) setError(err instanceof Error ? err.message : "Diagram could not be rendered");
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [source, theme, flowDir]);

  useEffect(() => {
    const vp = viewportRef.current;
    if (!vp || showSource) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      const rect = vp.getBoundingClientRect();
      const mx = event.clientX - rect.left;
      const my = event.clientY - rect.top;
      const factor = event.deltaY < 0 ? 1.1 : 0.9;
      setZoom((prevZoom) => {
        const nextZoom = Math.max(0.2, Math.min(4, Number((prevZoom * factor).toFixed(3))));
        const ratio = nextZoom / prevZoom;
        setPan((prevPan) => ({
          x: Math.round(mx - (mx - prevPan.x) * ratio),
          y: Math.round(my - (my - prevPan.y) * ratio),
        }));
        return nextZoom;
      });
    };
    vp.addEventListener("wheel", onWheel, { passive: false });
    return () => vp.removeEventListener("wheel", onWheel);
  }, [showSource]);

  if (!source.trim()) return <p className="p-4 text-muted">No diagram for this level.</p>;
  return (
    <div className={`relative h-full w-full overflow-hidden ${className}`}>
      <div className="absolute right-9 top-1.5 z-10 flex items-center gap-1">
        <span className="border border-line bg-surface/90 px-1.5 font-mono text-micro text-muted leading-6 tnum">
          {Math.round(zoom * 100)}%
        </span>
        <button type="button" className="icon-btn border border-line bg-surface" title="Zoom in" onClick={() => setZoom((z) => Math.min(4, Number((z + 0.2).toFixed(2))))}>
          <span className="material-symbols-outlined">zoom_in</span>
        </button>
        <button type="button" className="icon-btn border border-line bg-surface" title="Zoom out" onClick={() => setZoom((z) => Math.max(0.2, Number((z - 0.2).toFixed(2))))}>
          <span className="material-symbols-outlined">zoom_out</span>
        </button>
        <button type="button" className="icon-btn border border-line bg-surface" title="Re-align & fit diagram" onClick={realign}>
          <span className="material-symbols-outlined">center_focus_strong</span>
        </button>
        {isFlowchart && (
          <button
            type="button"
            className="icon-btn border border-line bg-surface"
            title="Toggle diagram alignment (Top-Bottom / Left-Right)"
            onClick={() => setFlowDir((d) => (d === "LR" ? "TB" : "LR"))}
          >
            <span className="material-symbols-outlined">swap_calls</span>
          </button>
        )}
        <button type="button" className={`icon-btn border border-line bg-surface ${showSource ? "text-primary" : ""}`} title="Mermaid source" onClick={() => setShowSource((v) => !v)}>
          <span className="material-symbols-outlined">code</span>
        </button>
      </div>
      {showSource ? (
        <pre className="h-full overflow-auto p-4 font-mono text-code text-muted">{source}</pre>
      ) : (
        <div
          ref={viewportRef}
          className={`h-full w-full overflow-hidden select-none ${dragging ? "cursor-grabbing" : "cursor-grab"}`}
          onMouseDown={(event) => {
            if (event.button !== 0) return;
            setDragging(true);
            dragStart.current = { x: event.clientX, y: event.clientY, px: pan.x, py: pan.y };
          }}
          onMouseMove={(event) => {
            if (!dragStart.current) return;
            setPan({
              x: dragStart.current.px + (event.clientX - dragStart.current.x),
              y: dragStart.current.py + (event.clientY - dragStart.current.y),
            });
          }}
          onMouseUp={() => {
            setDragging(false);
            dragStart.current = null;
          }}
          onMouseLeave={() => {
            setDragging(false);
            dragStart.current = null;
          }}
        >
          {error ? (
            <div className="p-4 space-y-2">
              <p className="text-critical text-body-md">Diagram could not be rendered: {error}</p>
              <pre className="overflow-auto font-mono text-code text-muted">{source}</pre>
            </div>
          ) : (
            <div
              ref={host}
              className="mermaid-host origin-top-left pointer-events-none"
              style={{ transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})` }}
            />
          )}
        </div>
      )}
    </div>
  );
}
