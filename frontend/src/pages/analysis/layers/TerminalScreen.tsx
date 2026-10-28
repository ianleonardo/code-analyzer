/* eslint-disable @typescript-eslint/no-explicit-any */
// Character-cell rendering of a CICS BMS map (3270) or a 5250 display-file record format, placed by row / column.

const LINE_PX = 18;
const PAD_PX = 8;

export function isTerminalScreen(d: any): boolean {
  return d?.platform === "CICS BMS" || d?.platform === "IBM i 5250";
}

export function TerminalScreen({ d, onLine }: { d: any; onLine?: (line: number) => void }) {
  const [rows, cols] = Array.isArray(d.size) && d.size.length === 2 ? (d.size as [number, number]) : [24, 80];
  // BMS POS addresses the attribute byte, so data starts one column later; DDS positions address the data itself.
  const shift = d.platform === "CICS BMS" ? 0 : -1;
  const constants: any[] = (d.constants ?? []).filter((c: any) => c.row && c.col);
  const fields: any[] = (d.fields ?? []).filter((f: any) => f.row && f.col);
  const ghostRows = [1, 2];
  const left = (col: number) => `calc(${Math.max(0, col + shift)}ch + ${PAD_PX}px)`;
  const top = (row: number) => (row - 1) * LINE_PX + PAD_PX;
  const fieldText = (f: any) => {
    const width = Math.max(1, Math.min(f.length || f.name?.length || 1, cols));
    if (f.initial) return f.initial.padEnd(width, " ").slice(0, width);
    return (f.widget?.startsWith("input") ? "_" : f.type?.startsWith("numeric") ? "9" : "X").repeat(width);
  };
  return (
    <section className="panel">
      <div className="panel-head">
        <span>{d.platform === "CICS BMS" ? `3270 map ${d.mapset ?? ""}.${d.map ?? ""}` : `5250 display ${d.file ?? ""}.${d.format ?? ""}`}</span>
        <span>{rows}×{cols} · {fields.length} fields</span>
      </div>
      <div className="overflow-x-auto p-2.5">
        <div
          className="relative rounded border border-line font-mono"
          style={{ width: `calc(${cols}ch + ${PAD_PX * 2}px)`, height: rows * LINE_PX + PAD_PX * 2, fontSize: 12, lineHeight: `${LINE_PX}px`, background: "#0b120e", color: "#7fe0a6" }}
          role="img"
          aria-label={`Terminal layout of ${d.title || "screen"}`}
        >
          {constants.map((c, i) => (
            <span key={`c${i}`} className="absolute whitespace-pre" style={{ left: left(c.col), top: top(c.row) }} title={`Constant · line ${c.line}`}>{c.text}</span>
          ))}
          {fields.map((f, i) => {
            const input = f.widget?.startsWith("input");
            const style = { left: left(f.col), top: top(f.row), color: input ? "#f0d074" : "#8cc8ff", borderBottom: input ? "1px solid #f0d07488" : undefined };
            return (
              <span key={`f${i}`}>
                <button
                  type="button"
                  className="absolute whitespace-pre text-left hover:bg-white/10"
                  style={style}
                  title={`${f.name} · ${f.widget} · ${f.type}${f.label ? ` · “${f.label}”` : ""}${f.validations?.length ? ` · ${f.validations.join(" ")}` : ""}`}
                  onClick={() => f.line && onLine?.(f.line)}
                >
                  {fieldText(f)}
                </button>
                {f.subfile && ghostRows.map((g) => f.row + g <= rows && (
                  <span key={g} className="absolute whitespace-pre" style={{ ...style, top: top(f.row + g), opacity: 0.35 }}>{fieldText(f)}</span>
                ))}
              </span>
            );
          })}
        </div>
        <p className="mt-1.5 flex flex-wrap gap-3 font-mono text-micro text-faint">
          <span><span style={{ color: "#f0d074" }}>___</span> input</span>
          <span><span style={{ color: "#8cc8ff" }}>XXX</span> output</span>
          <span><span style={{ color: "#7fe0a6" }}>Abc</span> constant</span>
          {fields.some((f) => f.subfile) && <span>faded rows = subfile repeats</span>}
          {(d.actions ?? []).length > 0 && <span>keys: {(d.actions ?? []).map((a: any) => a.label).join("  ")}</span>}
        </p>
      </div>
    </section>
  );
}
