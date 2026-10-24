import type { ReactNode } from "react";
import type { ImpactSeverity, RiskLabel } from "../../types";

const RISK: Record<string, string> = {
  LOW: "text-ok border-ok/50 bg-ok/10",
  MODERATE: "text-warning border-warning/50 bg-warning/10",
  HIGH: "text-critical border-critical/50 bg-critical/10",
  CRITICAL: "text-on-primary border-critical bg-critical",
};

export function RiskBadge({ label, score }: { label: RiskLabel | string; score?: number | null }) {
  if (!label) return <span className="font-mono text-micro text-faint">—</span>;
  return (
    <span className={`chip ${RISK[label] ?? RISK.MODERATE}`} title={score !== undefined && score !== null ? `risk score ${score}` : undefined}>
      {label}
      {score !== undefined && score !== null && <span className="tnum opacity-80">{score.toFixed(2)}</span>}
    </span>
  );
}

export function riskStroke(label: string) {
  return label === "LOW" ? "rgb(var(--ok))" : label === "MODERATE" ? "rgb(var(--warning))" : "rgb(var(--critical))";
}

/** Semi-circular gauge for the 0–1 risk score. */
export function RiskGauge({ score, label, size = 120 }: { score: number; label: string; size?: number }) {
  const value = Math.max(0, Math.min(1, score));
  const r = size / 2 - 8;
  const half = Math.PI * r;
  return (
    <svg width={size} height={size / 2 + 14} viewBox={`0 0 ${size} ${size / 2 + 14}`} role="img" aria-label={`Risk ${label} ${score}`}>
      <path d={`M 8 ${size / 2} A ${r} ${r} 0 0 1 ${size - 8} ${size / 2}`} fill="none" stroke="rgb(var(--line))" strokeWidth={8} strokeDasharray="3 2" />
      <path d={`M 8 ${size / 2} A ${r} ${r} 0 0 1 ${size - 8} ${size / 2}`} fill="none" stroke={riskStroke(label)} strokeWidth={8} strokeDasharray={`${half * value} ${half}`} />
      <text x={size / 2} y={size / 2 - 4} textAnchor="middle" className="font-mono" fontSize={18} fill={riskStroke(label)}>{label}</text>
      <text x={size / 2} y={size / 2 + 12} textAnchor="middle" className="font-mono" fontSize={10} fill="rgb(var(--muted))">score {score.toFixed(2)}</text>
    </svg>
  );
}

const SEV: Record<string, string> = {
  critical: "text-critical border-critical/50 bg-critical/10",
  high: "text-critical border-critical/40",
  medium: "text-warning border-warning/50 bg-warning/10",
  low: "text-info border-info/40 bg-info/10",
  info: "text-muted border-line-strong",
};

export function FindingSeverity({ severity }: { severity: ImpactSeverity | string }) {
  return <span className={`chip ${SEV[severity] ?? SEV.info}`}>{severity}</span>;
}

const REACH: Record<string, string> = {
  removed: "text-critical border-critical/50 bg-critical/10",
  added: "text-ok border-ok/50 bg-ok/10",
  direct: "text-warning border-warning/50 bg-warning/10",
  "same file": "text-warning border-warning/40",
  transitive: "text-info border-info/40 bg-info/10",
  linked: "text-muted border-line-strong",
};

export function ReachChip({ reach }: { reach: string }) {
  return <span className={`chip ${REACH[reach] ?? REACH.linked}`}>{reach}</span>;
}

const CHANGE: Record<string, string> = { A: "text-ok", added: "text-ok", D: "text-critical", removed: "text-critical", M: "text-warning", modified: "text-warning", R: "text-info", changed: "text-warning" };
const CHANGE_LABEL: Record<string, string> = { A: "added", D: "deleted", M: "modified", R: "renamed" };

export function ChangeChip({ change }: { change: string }) {
  return <span className={`chip border-current ${CHANGE[change] ?? "text-muted"}`}>{CHANGE_LABEL[change] ?? change}</span>;
}

export function KindChips({ kinds }: { kinds: string[] }) {
  return (
    <span className="inline-flex flex-wrap gap-1">
      {kinds.map((k) => <span key={k} className={`chip ${k === "code" ? "border-primary/40 text-primary" : k === "test" ? "border-ok/40 text-ok" : ["config", "infra", "ci", "migration"].includes(k) ? "border-warning/40 text-warning" : "border-line text-muted"}`}>{k}</span>)}
    </span>
  );
}

export function FlagChip({ flag }: { flag: string }) {
  const cls = flag === "contract" ? "text-critical border-critical/50" : flag.startsWith("+") ? "text-warning border-warning/50" : flag.startsWith("-") ? "text-muted border-line" : flag === "sensitive" ? "text-critical border-critical/40" : "text-muted border-line";
  return <span className={`chip normal-case ${cls}`}>{flag}</span>;
}

export function Stat({ label, value, sub, tone = "" }: { label: string; value: ReactNode; sub?: ReactNode; tone?: string }) {
  return (
    <div className="card">
      <div className="label-micro">{label}</div>
      <div className={`kpi ${tone}`}>{value}</div>
      {sub && <div className="font-mono text-micro text-faint">{sub}</div>}
    </div>
  );
}

export function basename(path: string) {
  return path.split("/").pop() ?? path;
}
