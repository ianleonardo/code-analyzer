import { Children, Component, isValidElement, useEffect, useMemo, useRef, useState, type ReactElement, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { renderMarkdown } from "../markdown";
import type { Layer } from "../types";

export const LAYER_TEXT: Record<string, string> = {
  api: "text-layer-api border-layer-api/40 bg-layer-api/15",
  service: "text-layer-service border-layer-service/40 bg-layer-service/15",
  data: "text-layer-data border-layer-data/40 bg-layer-data/15",
  ui: "text-layer-ui border-layer-ui/40 bg-layer-ui/15",
  shared: "text-layer-shared border-layer-shared/40 bg-layer-shared/15",
};
export const LAYER_DOT: Record<string, string> = {
  api: "bg-layer-api",
  service: "bg-layer-service",
  data: "bg-layer-data",
  ui: "bg-layer-ui",
  shared: "bg-layer-shared",
};
export const LAYER_VAR: Record<string, string> = { api: "layer-api", service: "layer-service", data: "layer-data", ui: "layer-ui", shared: "layer-shared" };

/** Canonical architectural layer pill (rounded, the only pill-shaped badge). */
export function LayerPill({ layer, count }: { layer: Layer | string; count?: number }) {
  return (
    <span className={`pill ${LAYER_TEXT[layer] ?? LAYER_TEXT.shared}`}>
      {layer}
      {count !== undefined && <span className="opacity-70 tnum">{count}</span>}
    </span>
  );
}

/** File role badge: rectangular micro-caps tag. */
export function RoleBadge({ role, confidence }: { role: string; confidence?: number }) {
  return (
    <span className="chip border-line-strong text-muted bg-elevated" title={confidence !== undefined ? `confidence ${Math.round(confidence * 100)}%` : undefined}>
      {role}
      {confidence !== undefined && <span className="text-primary tnum">{Math.round(confidence * 100)}%</span>}
    </span>
  );
}

const CONCEPT: Record<string, string> = {
  "db-read": "bg-c-db-read",
  "db-write": "bg-c-db-write",
  "http-call": "bg-c-http",
  "env-access": "bg-c-env",
  validation: "bg-c-validation",
  "tx-scope": "bg-c-tx",
};

export function ConceptTag({ concept }: { concept: string }) {
  return (
    <span className="inline-flex items-center gap-1 font-mono text-micro text-muted">
      <span className={`h-1 w-1 ${CONCEPT[concept] ?? "bg-faint"}`} />
      {concept}
    </span>
  );
}

const SEVERITY: Record<string, string> = {
  critical: "text-critical border-critical/50 bg-critical/10",
  warning: "text-warning border-warning/50 bg-warning/10",
  info: "text-info border-info/40 bg-info/10",
};
export const SEVERITY_ICON: Record<string, string> = { critical: "error", warning: "warning", info: "info" };

export function SeverityBadge({ severity }: { severity: string }) {
  return <span className={`chip ${SEVERITY[severity] ?? SEVERITY.info}`}>{severity}</span>;
}

const STATUS: Record<string, string> = {
  completed: "text-ok border-ok/40 bg-ok/10",
  running: "text-primary border-primary/40 bg-primary/10",
  queued: "text-muted border-line-strong bg-elevated",
  failed: "text-critical border-critical/40 bg-critical/10",
  cancelled: "text-warning border-warning/40 bg-warning/10",
};

export function StatusBadge({ status, progress }: { status: string; progress?: number }) {
  return (
    <span className={`chip ${STATUS[status] ?? STATUS.queued}`}>
      {status === "running" && <span className="h-1.5 w-1.5 rounded-full bg-primary pulse-dot" />}
      {status}
      {status === "running" && progress !== undefined && <span className="tnum">{Math.round(progress)}%</span>}
    </span>
  );
}

const PROVIDER: Record<string, { icon: string; cls: string; label: string }> = {
  github: { icon: "hub", cls: "text-status-git", label: "GitHub" },
  bitbucket: { icon: "cloud", cls: "text-status-git", label: "Bitbucket" },
  bitbucket_server: { icon: "dns", cls: "text-status-git", label: "Bitbucket Server" },
  git: { icon: "account_tree", cls: "text-status-git", label: "Git" },
  zip: { icon: "folder_zip", cls: "text-status-archive", label: "Archive" },
  local: { icon: "folder_open", cls: "text-status-local", label: "Local mount" },
};

export function SourceBadge({ source, label }: { source: string; label?: string }) {
  const item = PROVIDER[source] ?? PROVIDER.git;
  return (
    <span className="inline-flex items-center gap-1 font-mono text-micro text-muted whitespace-nowrap">
      <span className={`h-1.5 w-1.5 rounded-full ${item.cls.replace("text-", "bg-")}`} />
      <span className={`material-symbols-outlined icon-sm ${item.cls}`}>{item.icon}</span>
      {label ?? item.label}
    </span>
  );
}

export function healthColor(score: number | null | undefined) {
  if (score === null || score === undefined) return "text-faint";
  if (score >= 80) return "text-ok";
  if (score >= 50) return "text-warning";
  return "text-critical";
}

export function healthStroke(score: number | null | undefined) {
  if (score === null || score === undefined) return "rgb(var(--faint))";
  if (score >= 80) return "rgb(var(--ok))";
  if (score >= 50) return "rgb(var(--warning))";
  return "rgb(var(--critical))";
}

/** Segmented circular health gauge (0–100). */
export function HealthGauge({ score, size = 44, label = true }: { score: number | null | undefined; size?: number; label?: boolean }) {
  const value = Math.max(0, Math.min(100, score ?? 0));
  const radius = size / 2 - 4;
  const circumference = 2 * Math.PI * radius;
  return (
    <span className="relative inline-flex items-center justify-center" style={{ width: size, height: size }} title={`Health ${score ?? "—"}/100`}>
      <svg width={size} height={size} className="-rotate-90">
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke="rgb(var(--line))" strokeWidth={3} strokeDasharray="2 2" />
        <circle cx={size / 2} cy={size / 2} r={radius} fill="none" stroke={healthStroke(score)} strokeWidth={3} strokeDasharray={`${(circumference * value) / 100} ${circumference}`} />
      </svg>
      {label && <span className={`absolute font-mono text-micro tnum ${healthColor(score)}`}>{score === null || score === undefined ? "—" : Math.round(value)}</span>}
    </span>
  );
}

/** Horizontal segmented meter. */
export function Meter({ value, max = 100, className = "", color }: { value: number; max?: number; className?: string; color?: string }) {
  const pct = Math.max(0, Math.min(100, (value / (max || 1)) * 100));
  return (
    <div className={`h-1.5 w-full bg-elevated ${className}`}>
      <div className="h-full" style={{ width: `${pct}%`, background: color ?? "rgb(var(--primary))" }} />
    </div>
  );
}

export function Markdown({ text, className = "" }: { text: string; className?: string }) {
  return <div className={`md ${className}`} dangerouslySetInnerHTML={{ __html: renderMarkdown(text) }} />;
}

export function Spinner({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-2 font-mono text-micro text-muted">
      <span className="material-symbols-outlined icon-sm animate-spin text-primary">progress_activity</span>
      {label}
    </span>
  );
}

export function Empty({ icon = "inventory_2", title, children }: { icon?: string; title: string; children?: ReactNode }) {
  return (
    <div className="flex flex-col items-center justify-center gap-2 py-12 text-center text-muted">
      <span className="material-symbols-outlined icon-lg text-faint">{icon}</span>
      <p className="font-medium text-ink">{title}</p>
      {children && <div className="max-w-md text-body-md">{children}</div>}
    </div>
  );
}

export function ErrorNote({ message }: { message: string }) {
  if (!message) return null;
  return (
    <p className="flex items-start gap-2 border border-critical/40 bg-critical/10 px-2 py-1.5 text-critical text-body-md" role="alert">
      <span className="material-symbols-outlined icon-sm mt-0.5">error</span>
      <span>{message}</span>
    </p>
  );
}

type ResizeDir = "n" | "s" | "e" | "w" | "ne" | "nw" | "se" | "sw";
type FloatingRect = { x: number; y: number; w: number; h: number };

let floatingZCounter = 65;

function clampFloatingRect(rect: FloatingRect): FloatingRect {
  const vw = typeof window !== "undefined" ? window.innerWidth : 1280;
  const vh = typeof window !== "undefined" ? window.innerHeight : 800;
  const w = Math.max(300, Math.min(vw - 24, Math.round(rect.w)));
  const h = Math.max(200, Math.min(vh - 24, Math.round(rect.h)));
  const x = Math.max(8, Math.min(vw - 80, Math.round(rect.x)));
  const y = Math.max(8, Math.min(vh - 48, Math.round(rect.y)));
  return { x, y, w, h };
}

function ResizeHandles({ onStartResize }: { onStartResize: (dir: ResizeDir, e: React.MouseEvent) => void }) {
  return (
    <>
      <div className="absolute -top-1 left-2 right-2 z-30 h-2 cursor-ns-resize" onMouseDown={(e) => onStartResize("n", e)} />
      <div className="absolute -bottom-1 left-2 right-2 z-30 h-2 cursor-ns-resize" onMouseDown={(e) => onStartResize("s", e)} />
      <div className="absolute -left-1 bottom-2 top-2 z-30 w-2 cursor-ew-resize" onMouseDown={(e) => onStartResize("w", e)} />
      <div className="absolute -right-1 bottom-2 top-2 z-30 w-2 cursor-ew-resize" onMouseDown={(e) => onStartResize("e", e)} />
      <div className="absolute -left-1.5 -top-1.5 z-40 h-3 w-3 cursor-nwse-resize" onMouseDown={(e) => onStartResize("nw", e)} />
      <div className="absolute -right-1.5 -top-1.5 z-40 h-3 w-3 cursor-nesw-resize" onMouseDown={(e) => onStartResize("ne", e)} />
      <div className="absolute -bottom-1.5 -left-1.5 z-40 h-3 w-3 cursor-nesw-resize" onMouseDown={(e) => onStartResize("sw", e)} />
      <div
        className="absolute bottom-0 right-0 z-40 flex h-4 w-4 cursor-nwse-resize items-end justify-end pr-0.5 pb-0.5 text-faint hover:text-primary"
        title="Drag to resize"
        onMouseDown={(e) => onStartResize("se", e)}
      >
        <svg width="10" height="10" viewBox="0 0 10 10" className="pointer-events-none">
          <path d="M9 1L1 9M9 5L5 9M9 8.5L8.5 9" stroke="currentColor" strokeWidth="1.3" strokeLinecap="round" />
        </svg>
      </div>
    </>
  );
}

export function Modal({ title, onClose, children, width = "max-w-lg", footer }: { title: ReactNode; onClose: () => void; children: ReactNode; width?: string; footer?: ReactNode }) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [floating, setFloating] = useState<FloatingRect | null>(null);
  const [maximized, setMaximized] = useState(false);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => event.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const ensureFloating = (): FloatingRect => {
    if (floating) return floating;
    const r = boxRef.current?.getBoundingClientRect();
    const initial = r
      ? { x: r.left, y: r.top, w: r.width, h: r.height }
      : { x: Math.max(40, (window.innerWidth - 640) / 2), y: 80, w: 640, h: 480 };
    setFloating(initial);
    return initial;
  };

  const startMove = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest("button") || maximized) return;
    e.preventDefault();
    const start = ensureFloating();
    const sx = e.clientX;
    const sy = e.clientY;
    const onMove = (ev: MouseEvent) => {
      setFloating(clampFloatingRect({ ...start, x: start.x + (ev.clientX - sx), y: start.y + (ev.clientY - sy) }));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const startResize = (dir: ResizeDir, e: React.MouseEvent) => {
    if (maximized) return;
    e.preventDefault();
    e.stopPropagation();
    const start = ensureFloating();
    const sx = e.clientX;
    const sy = e.clientY;
    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - sx;
      const dy = ev.clientY - sy;
      let { x, y, w, h } = start;
      if (dir.includes("e")) w = Math.max(320, start.w + dx);
      if (dir.includes("s")) h = Math.max(200, start.h + dy);
      if (dir.includes("w")) {
        const nextW = Math.max(320, start.w - dx);
        x = start.x + (start.w - nextW);
        w = nextW;
      }
      if (dir.includes("n")) {
        const nextH = Math.max(200, start.h - dy);
        y = start.y + (start.h - nextH);
        h = nextH;
      }
      setFloating(clampFloatingRect({ x, y, w, h }));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const style: React.CSSProperties | undefined = maximized
    ? { position: "fixed", left: 12, top: 12, width: "calc(100vw - 24px)", height: "calc(100vh - 24px)", maxHeight: "none" }
    : floating
      ? { position: "fixed", left: floating.x, top: floating.y, width: floating.w, height: floating.h, maxHeight: "none" }
      : undefined;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-canvas/80 p-4 backdrop-blur-[4px]" onMouseDown={(event) => event.target === event.currentTarget && onClose()}>
      <div
        ref={boxRef}
        role="dialog"
        aria-modal="true"
        style={style}
        className={`relative ${floating || maximized ? "" : `w-full ${width} max-h-[90vh]`} flex flex-col border border-line-strong bg-elevated shadow-[0_4px_16px_-2px_rgba(0,0,0,0.6)]`}
      >
        <div
          onMouseDown={startMove}
          onDoubleClick={() => setMaximized((m) => !m)}
          title="Drag to move · Double-click to maximize"
          className="flex h-9 shrink-0 cursor-move select-none items-center justify-between border-b border-line px-3"
        >
          <h2 className="flex min-w-0 items-center gap-1.5 truncate text-head-sm">
            <span className="material-symbols-outlined icon-sm text-faint">drag_indicator</span>
            <span className="truncate">{title}</span>
          </h2>
          <div className="flex items-center gap-1">
            {(floating || maximized) && (
              <button
                type="button"
                className="icon-btn"
                title="Reset position & size"
                onClick={() => {
                  setFloating(null);
                  setMaximized(false);
                }}
              >
                <span className="material-symbols-outlined icon-sm">restart_alt</span>
              </button>
            )}
            <button
              type="button"
              className="icon-btn"
              title={maximized ? "Restore size" : "Maximize"}
              onClick={() => setMaximized((m) => !m)}
            >
              <span className="material-symbols-outlined icon-sm">{maximized ? "close_fullscreen" : "open_in_full"}</span>
            </button>
            <button type="button" className="icon-btn" aria-label="Close" title="Close" onClick={onClose}>
              <span className="material-symbols-outlined">close</span>
            </button>
          </div>
        </div>
        <div className="min-h-0 flex-1 overflow-auto p-3">{children}</div>
        {footer && <div className="flex shrink-0 justify-end gap-2 border-t border-line px-3 py-2">{footer}</div>}
        {!maximized && <ResizeHandles onStartResize={startResize} />}
      </div>
    </div>
  );
}

export function ConfirmDialog({ title, message, confirmLabel = "Delete", onConfirm, onClose, busy }: { title: string; message: ReactNode; confirmLabel?: string; onConfirm: () => void; onClose: () => void; busy?: boolean }) {
  return (
    <Modal
      title={title}
      onClose={onClose}
      width="max-w-md"
      footer={
        <>
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-danger" disabled={busy} onClick={onConfirm}>{confirmLabel}</button>
        </>
      }
    >
      <div className="text-body-md text-muted">{message}</div>
    </Modal>
  );
}

export function Field({ label, children, hint }: { label: string; children: ReactNode; hint?: ReactNode }) {
  return (
    <label className="block">
      <span className="label-micro">{label}</span>
      <div className="mt-1">{children}</div>
      {hint && <span className="mt-1 block text-body-sm text-faint">{hint}</span>}
    </label>
  );
}

export function SectionTitle({ children, right }: { children: ReactNode; right?: ReactNode }) {
  return (
    <div className="mb-2 flex items-center justify-between gap-2">
      <h3 className="label-micro">{children}</h3>
      {right}
    </div>
  );
}

export function ShortSha({ sha }: { sha: string }) {
  if (!sha) return <span className="text-faint">—</span>;
  return <span className="font-mono text-code text-primary tnum">{sha.slice(0, 8)}</span>;
}

export function Breadcrumb({ path, scope }: { path: string; scope?: string }) {
  return (
    <span className="font-mono text-code truncate">
      <span className="text-faint"># </span>
      <span className="text-muted">{path}</span>
      {scope && (
        <>
          <span className="text-faint"> :: </span>
          <span className="font-semibold text-primary">{scope}</span>
        </>
      )}
    </span>
  );
}

export function PageHeader({ title, subtitle, right, icon }: { title: ReactNode; subtitle?: ReactNode; right?: ReactNode; icon?: string }) {
  return (
    <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line bg-surface px-4 py-3">
      <div className="min-w-0">
        <h1 className="flex items-center gap-2 text-head-lg">
          {icon && <span className="material-symbols-outlined icon-lg text-primary">{icon}</span>}
          {title}
        </h1>
        {subtitle && <p className="mt-0.5 text-body-md text-muted">{subtitle}</p>}
      </div>
      {right && <div className="flex flex-wrap items-center gap-2">{right}</div>}
    </div>
  );
}

function SubWindowPane({
  title,
  allowPopout = true,
  isPrimary,
  siblingPopped,
  sizeStyle,
  popped,
  onPopChange,
  children,
}: {
  title: string;
  allowPopout?: boolean;
  isPrimary: boolean;
  siblingPopped: boolean;
  sizeStyle: React.CSSProperties;
  popped: boolean;
  onPopChange: (next: boolean) => void;
  children: ReactNode;
}) {
  const paneRef = useRef<HTMLDivElement>(null);
  const [rect, setRect] = useState<FloatingRect | null>(null);
  const [maximized, setMaximized] = useState(false);
  const [zIndex, setZIndex] = useState(70);

  const bringToFront = () => {
    floatingZCounter += 1;
    setZIndex(floatingZCounter);
  };

  const popOut = () => {
    const domRect = paneRef.current?.getBoundingClientRect();
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const w = Math.max(460, Math.min(vw - 64, Math.round((domRect?.width ?? 680) * 1.08)));
    const h = Math.max(320, Math.min(vh - 64, Math.round((domRect?.height ?? 480) * 1.08)));
    const x = domRect
      ? Math.max(24, Math.min(vw - w - 24, Math.round(domRect.left + 20)))
      : Math.round((vw - w) / 2);
    const y = domRect
      ? Math.max(24, Math.min(vh - h - 24, Math.round(domRect.top + 20)))
      : Math.round((vh - h) / 2);
    setRect(clampFloatingRect({ x, y, w, h }));
    setMaximized(false);
    bringToFront();
    onPopChange(true);
  };

  const dockBack = () => {
    setMaximized(false);
    onPopChange(false);
  };

  useEffect(() => {
    if (!popped) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMaximized(false);
        onPopChange(false);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [popped, onPopChange]);

  const startMove = (e: React.MouseEvent) => {
    if ((e.target as HTMLElement).closest("button") || maximized || !rect) return;
    e.preventDefault();
    bringToFront();
    const start = rect;
    const sx = e.clientX;
    const sy = e.clientY;
    const onMove = (ev: MouseEvent) => {
      setRect(clampFloatingRect({ ...start, x: start.x + (ev.clientX - sx), y: start.y + (ev.clientY - sy) }));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const startResize = (dir: ResizeDir, e: React.MouseEvent) => {
    if (maximized || !rect) return;
    e.preventDefault();
    e.stopPropagation();
    bringToFront();
    const start = rect;
    const sx = e.clientX;
    const sy = e.clientY;
    const onMove = (ev: MouseEvent) => {
      const dx = ev.clientX - sx;
      const dy = ev.clientY - sy;
      let { x, y, w, h } = start;
      if (dir.includes("e")) w = Math.max(300, start.w + dx);
      if (dir.includes("s")) h = Math.max(200, start.h + dy);
      if (dir.includes("w")) {
        const nextW = Math.max(300, start.w - dx);
        x = start.x + (start.w - nextW);
        w = nextW;
      }
      if (dir.includes("n")) {
        const nextH = Math.max(200, start.h - dy);
        y = start.y + (start.h - nextH);
        h = nextH;
      }
      setRect(clampFloatingRect({ x, y, w, h }));
    };
    const onUp = () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
  };

  const containerStyle: React.CSSProperties | undefined = popped
    ? maximized
      ? {
          position: "fixed",
          left: 12,
          top: 12,
          width: "calc(100vw - 24px)",
          height: "calc(100vh - 24px)",
          zIndex,
        }
      : rect
        ? {
            position: "fixed",
            left: rect.x,
            top: rect.y,
            width: rect.w,
            height: rect.h,
            zIndex,
          }
        : undefined
    : isPrimary && !siblingPopped
      ? sizeStyle
      : undefined;

  const containerClass = popped
    ? "flex flex-col border-2 border-primary/70 bg-surface shadow-[0_16px_48px_rgba(0,0,0,0.65)] overflow-hidden"
    : `group/subwin relative flex h-full min-h-0 min-w-0 flex-col overflow-hidden ${
        isPrimary && !siblingPopped ? "shrink-0" : "flex-1"
      }`;

  return (
    <div ref={paneRef} style={containerStyle} className={containerClass} onMouseDown={() => popped && bringToFront()}>
      {popped ? (
        <div
          onMouseDown={startMove}
          onDoubleClick={() => setMaximized((m) => !m)}
          title="Drag to move subwindow · Double-click to maximize"
          className="flex h-8 shrink-0 cursor-move select-none items-center justify-between border-b border-line bg-elevated px-2.5"
        >
          <div className="flex min-w-0 items-center gap-1.5 font-mono text-code font-medium text-ink">
            <span className="material-symbols-outlined icon-sm text-primary">drag_indicator</span>
            <span className="truncate">{title}</span>
            <span className="chip border-primary/40 bg-primary/10 text-primary">floating</span>
          </div>
          <div className="flex items-center gap-1">
            <button
              type="button"
              className="icon-btn h-6 w-6"
              title={maximized ? "Restore floating size" : "Maximize"}
              onClick={() => setMaximized((m) => !m)}
            >
              <span className="material-symbols-outlined icon-sm">{maximized ? "close_fullscreen" : "open_in_full"}</span>
            </button>
            <button
              type="button"
              className="btn h-6 px-2 text-micro"
              title="Close popup and dock back to original place"
              onClick={dockBack}
            >
              <span className="material-symbols-outlined icon-sm">pip_exit</span>
              Dock
            </button>
            <button
              type="button"
              className="icon-btn h-6 w-6 hover:!text-critical"
              title="Close popup (return to original place)"
              onClick={dockBack}
            >
              <span className="material-symbols-outlined icon-sm">close</span>
            </button>
          </div>
        </div>
      ) : (
        allowPopout && (
          <button
            type="button"
            onClick={popOut}
            title={`Pop out "${title}" (move & resize)`}
            className="icon-btn absolute right-1.5 top-1.5 z-30 h-6 w-6 border border-line bg-surface/90 text-muted opacity-45 backdrop-blur-[2px] transition-opacity group-hover/subwin:opacity-100 hover:!border-primary hover:!text-primary hover:!opacity-100"
          >
            <span className="material-symbols-outlined icon-sm">open_in_new</span>
          </button>
        )
      )}
      <div className="flex h-full w-full min-h-0 min-w-0 flex-1 flex-col overflow-hidden">{children}</div>
      {popped && !maximized && <ResizeHandles onStartResize={startResize} />}
    </div>
  );
}

/**
 * Resizable split view (horizontal left/right or vertical top/bottom).
 * Double-click the divider handle to reset to initialSize.
 * Each subwindow can also be popped out into a movable, resizable floating window and docked back on close.
 */
export function SplitPane({
  direction = "horizontal",
  primary = "first",
  initialSize = 320,
  minSize = 140,
  maxSize = 900,
  first,
  second,
  firstTitle = "Subwindow 1",
  secondTitle = "Subwindow 2",
  allowPopout = true,
  className = "",
}: {
  direction?: "horizontal" | "vertical";
  primary?: "first" | "second";
  initialSize?: number;
  minSize?: number;
  maxSize?: number;
  first: ReactNode;
  second: ReactNode;
  firstTitle?: string;
  secondTitle?: string;
  allowPopout?: boolean;
  className?: string;
}) {
  const [size, setSize] = useState(initialSize);
  const [dragging, setDragging] = useState(false);
  const [firstPopped, setFirstPopped] = useState(false);
  const [secondPopped, setSecondPopped] = useState(false);
  const containerRef = useRef<HTMLDivElement>(null);
  const isHorizontal = direction === "horizontal";

  useEffect(() => {
    if (!dragging) return;
    const onMove = (e: MouseEvent) => {
      if (!containerRef.current) return;
      const rect = containerRef.current.getBoundingClientRect();
      const total = isHorizontal ? rect.width : rect.height;
      const pos = isHorizontal ? e.clientX - rect.left : e.clientY - rect.top;
      const raw = primary === "first" ? pos : total - pos;
      const effectiveMax = Math.min(maxSize, Math.max(minSize, total - 120));
      setSize(Math.max(minSize, Math.min(effectiveMax, Math.round(raw))));
    };
    const onUp = () => setDragging(false);
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    document.body.style.cursor = isHorizontal ? "col-resize" : "row-resize";
    document.body.style.userSelect = "none";
    return () => {
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
  }, [dragging, isHorizontal, primary, minSize, maxSize]);

  const sizeStyle = isHorizontal ? { width: `${size}px` } : { height: `${size}px` };
  const bothDocked = !firstPopped && !secondPopped;

  return (
    <div
      ref={containerRef}
      className={`flex min-h-0 min-w-0 ${isHorizontal ? "h-full flex-row" : "h-full flex-col"} ${className}`}
    >
      <SubWindowPane
        title={firstTitle}
        allowPopout={allowPopout}
        isPrimary={primary === "first"}
        siblingPopped={secondPopped}
        sizeStyle={sizeStyle}
        popped={firstPopped}
        onPopChange={setFirstPopped}
      >
        {first}
      </SubWindowPane>
      {bothDocked && (
        <div
          role="separator"
          aria-orientation={isHorizontal ? "vertical" : "horizontal"}
          title="Drag to resize · Double-click to reset"
          onMouseDown={(e) => {
            e.preventDefault();
            setDragging(true);
          }}
          onDoubleClick={() => setSize(initialSize)}
          className={`group relative z-20 flex shrink-0 items-center justify-center transition-colors ${
            isHorizontal
              ? "w-1.5 cursor-col-resize border-x border-line/60 bg-surface hover:bg-primary/25"
              : "h-1.5 cursor-row-resize border-y border-line/60 bg-surface hover:bg-primary/25"
          } ${dragging ? "!bg-primary/40" : ""}`}
        >
          <span
            className={`rounded-full bg-line-strong transition-colors group-hover:bg-primary ${
              isHorizontal ? "h-6 w-0.5" : "h-0.5 w-6"
            } ${dragging ? "!bg-primary" : ""}`}
          />
        </div>
      )}
      <SubWindowPane
        title={secondTitle}
        allowPopout={allowPopout}
        isPrimary={primary === "second"}
        siblingPopped={firstPopped}
        sizeStyle={sizeStyle}
        popped={secondPopped}
        onPopChange={setSecondPopped}
      >
        {second}
      </SubWindowPane>
      {firstPopped && secondPopped && (
        <div className="flex flex-1 flex-col items-center justify-center gap-2 border border-dashed border-line p-6 text-center text-muted">
          <span className="material-symbols-outlined icon-lg text-faint">picture_in_picture_alt</span>
          <p className="font-mono text-code">Both subwindows are currently floating.</p>
          <div className="flex gap-2">
            <button type="button" className="btn h-7" onClick={() => setFirstPopped(false)}>
              <span className="material-symbols-outlined icon-sm">pip_exit</span>Dock {firstTitle}
            </button>
            <button type="button" className="btn h-7" onClick={() => setSecondPopped(false)}>
              <span className="material-symbols-outlined icon-sm">pip_exit</span>Dock {secondTitle}
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

type ParsedOption = { value: string; label: ReactNode; textLabel: string; group?: string; disabled?: boolean };

function extractOptions(nodes: ReactNode, group?: string): ParsedOption[] {
  const list: ParsedOption[] = [];
  Children.forEach(nodes, (child) => {
    if (!isValidElement(child)) return;
    const el = child as ReactElement<{ value?: string | number; label?: string; disabled?: boolean; children?: ReactNode }>;
    if (el.type === "optgroup") {
      list.push(...extractOptions(el.props.children, el.props.label ?? ""));
    } else if (el.type === "option") {
      const val = el.props.value !== undefined ? String(el.props.value) : String(el.props.children ?? "");
      const text = typeof el.props.children === "string" || typeof el.props.children === "number"
        ? String(el.props.children)
        : Array.isArray(el.props.children)
        ? el.props.children.map((c) => (typeof c === "string" || typeof c === "number" ? String(c) : "")).join("")
        : val;
      list.push({ value: val, label: el.props.children, textLabel: text, group, disabled: el.props.disabled });
    } else if (el.props?.children) {
      list.push(...extractOptions(el.props.children, group));
    }
  });
  return list;
}

/**
 * Theme-consistent custom dropdown replacement for native <select>.
 * Renders its popup in a portal using the active theme's surface/elevated/primary tokens.
 */
export function Select({
  value,
  onChange,
  children,
  className = "",
  disabled = false,
  "aria-label": ariaLabel,
}: {
  value: string | number;
  onChange: (event: { target: { value: string } }) => void;
  children: ReactNode;
  className?: string;
  disabled?: boolean;
  "aria-label"?: string;
}) {
  const [open, setOpen] = useState(false);
  const [rect, setRect] = useState<{ top: number; left: number; width: number; openUp: boolean } | null>(null);
  const btnRef = useRef<HTMLButtonElement>(null);
  const menuRef = useRef<HTMLDivElement>(null);

  const options = useMemo(() => extractOptions(children), [children]);
  const strValue = String(value ?? "");
  const current = options.find((o) => o.value === strValue) ?? options[0];

  const updatePosition = () => {
    if (!btnRef.current) return;
    const r = btnRef.current.getBoundingClientRect();
    const spaceBelow = window.innerHeight - r.bottom;
    const openUp = spaceBelow < 240 && r.top > spaceBelow;
    setRect({
      top: openUp ? r.top - 4 : r.bottom + 4,
      left: r.left,
      width: r.width,
      openUp,
    });
  };

  useEffect(() => {
    if (!open) return;
    updatePosition();
    const onDown = (e: MouseEvent) => {
      if (btnRef.current?.contains(e.target as Node) || menuRef.current?.contains(e.target as Node)) return;
      setOpen(false);
    };
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setOpen(false);
    };
    const onScrollOrResize = (e: Event) => {
      if (menuRef.current && e.target instanceof Node && menuRef.current.contains(e.target)) return;
      updatePosition();
    };
    window.addEventListener("mousedown", onDown);
    window.addEventListener("keydown", onKey);
    window.addEventListener("resize", onScrollOrResize);
    window.addEventListener("scroll", onScrollOrResize, true);
    return () => {
      window.removeEventListener("mousedown", onDown);
      window.removeEventListener("keydown", onKey);
      window.removeEventListener("resize", onScrollOrResize);
      window.removeEventListener("scroll", onScrollOrResize, true);
    };
  }, [open]);

  let lastGroup: string | undefined;

  return (
    <>
      <button
        ref={btnRef}
        type="button"
        disabled={disabled}
        aria-label={ariaLabel}
        aria-haspopup="listbox"
        aria-expanded={open}
        onClick={() => !disabled && setOpen((o) => !o)}
        className={`${className || "field"} flex items-center justify-between gap-1.5 bg-inset text-ink font-sans text-left cursor-pointer select-none ${
          open ? "border-primary" : ""
        }`}
      >
        <span className="truncate flex-1">{current ? current.label : ""}</span>
        <span className={`material-symbols-outlined icon-sm text-muted transition-transform ${open ? "rotate-180 text-primary" : ""}`}>
          expand_more
        </span>
      </button>
      {open &&
        rect &&
        createPortal(
          <div
            ref={menuRef}
            role="listbox"
            style={{
              position: "fixed",
              left: `${rect.left}px`,
              top: rect.openUp ? undefined : `${rect.top}px`,
              bottom: rect.openUp ? `${window.innerHeight - rect.top}px` : undefined,
              minWidth: `${rect.width}px`,
              maxWidth: `${Math.max(rect.width, 420)}px`,
            }}
            className="z-[100] max-h-64 overflow-auto rounded-sm border border-line-strong bg-surface py-1 text-ink shadow-[0_8px_24px_-4px_rgba(0,0,0,0.35)]"
          >
            {options.map((opt, idx) => {
              const showGroup = opt.group !== undefined && opt.group !== lastGroup;
              lastGroup = opt.group;
              const isSelected = opt.value === strValue;
              return (
                <div key={`${opt.group ?? ""}:${opt.value}:${idx}`}>
                  {showGroup && (
                    <div className="border-t border-line/60 first:border-t-0 bg-elevated/60 px-2.5 py-1 font-mono text-micro uppercase text-faint">
                      {opt.group}
                    </div>
                  )}
                  <button
                    type="button"
                    role="option"
                    aria-selected={isSelected}
                    disabled={opt.disabled}
                    onClick={() => {
                      onChange({ target: { value: opt.value } });
                      setOpen(false);
                    }}
                    className={`flex w-full items-center justify-between gap-2 px-2.5 py-1.5 text-left font-sans text-body-md transition-colors ${
                      isSelected
                        ? "bg-primary/15 text-primary font-medium"
                        : "text-ink hover:bg-elevated hover:text-primary"
                    } ${opt.disabled ? "opacity-40 cursor-not-allowed" : "cursor-pointer"}`}
                  >
                    <span className="truncate">{opt.label}</span>
                    {isSelected && <span className="material-symbols-outlined icon-sm text-primary shrink-0">check</span>}
                  </button>
                </div>
              );
            })}
          </div>,
          document.body,
        )}
    </>
  );
}

export interface ErrorBoundaryProps {
  children: ReactNode;
  fallback?: ReactNode | ((error: Error, reset: () => void) => ReactNode);
  name?: string;
  onReset?: () => void;
}

export interface ErrorBoundaryState {
  hasError: boolean;
  error: Error | null;
}

export class ErrorBoundary extends Component<ErrorBoundaryProps, ErrorBoundaryState> {
  constructor(props: ErrorBoundaryProps) {
    super(props);
    this.state = { hasError: false, error: null };
  }

  static getDerivedStateFromError(error: Error): ErrorBoundaryState {
    return { hasError: true, error };
  }

  componentDidCatch(error: Error, errorInfo: any) {
    console.error(`[ErrorBoundary${this.props.name ? `:${this.props.name}` : ""}] caught render error:`, error, errorInfo);
  }

  reset = () => {
    this.setState({ hasError: false, error: null });
    this.props.onReset?.();
  };

  render() {
    if (this.state.hasError) {
      if (typeof this.props.fallback === "function") {
        return this.props.fallback(this.state.error || new Error("Unknown error"), this.reset);
      }
      if (this.props.fallback) {
        return this.props.fallback;
      }
      return (
        <div className="rounded border border-critical/40 bg-critical/10 p-4 text-ink">
          <div className="flex items-center gap-2 text-critical font-medium">
            <span className="material-symbols-outlined icon-md">error</span>
            <span>Failed to render {this.props.name || "content"}</span>
          </div>
          <div className="mt-2 font-mono text-micro text-critical/90 whitespace-pre-wrap break-all">
            {this.state.error?.message || "An unexpected error occurred while rendering this component."}
          </div>
          <div className="mt-3">
            <button
              type="button"
              className="btn btn-secondary h-7 text-micro"
              onClick={this.reset}
            >
              Try again
            </button>
          </div>
        </div>
      );
    }
    return this.props.children;
  }
}

