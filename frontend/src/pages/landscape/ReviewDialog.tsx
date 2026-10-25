import { useEffect, useState } from "react";
import { api, errorText, post } from "../../api";
import { CodeView } from "../../components/CodeView";
import { ErrorNote, Markdown, Modal, Spinner } from "../../components/ui";
import { KIND_META, STATUS_META, type LandscapeLink, type ReviewGroup } from "./types";

export function ConfidencePill({ link }: { link: Pick<LandscapeLink, "confidence" | "score"> }) {
  if (!link.confidence) return null;
  const color = link.confidence === "high" ? "text-ok border-ok/50" : link.confidence === "medium" ? "text-warning border-warning/50" : "text-critical border-critical/50";
  return <span className={`chip ${color}`} title={`score ${link.score}`}>{link.confidence} {link.score.toFixed(2)}</span>;
}

export function StatusPill({ status }: { status: string }) {
  const meta = STATUS_META[status] ?? { label: status, color: "text-muted border-line" };
  return <span className={`chip ${meta.color}`}>{meta.label}</span>;
}

export function KindChip({ kind }: { kind: string }) {
  const meta = KIND_META[kind as keyof typeof KIND_META];
  if (!meta) return <span className="chip border-line text-muted">{kind}</span>;
  return <span className={`chip ${meta.color}`}><span className="material-symbols-outlined text-[12px]">{meta.icon}</span>{meta.label}</span>;
}

/** Source code around a link end, loaded from the stored analysis. */
export function CodeModal({ analysisId, path, line, title, onClose }: { analysisId: string; path: string; line: number; title: string; onClose: () => void }) {
  const [content, setContent] = useState<string | null>(null);
  const [error, setError] = useState("");
  useEffect(() => {
    api<{ content: string }>(`/api/analyses/${analysisId}/catalog/source?path=${encodeURIComponent(path)}`).then((d) => setContent(d.content)).catch((err) => setError(errorText(err)));
  }, [analysisId, path]);
  return (
    <Modal title={<span className="font-mono">{title} · {path}{line ? `:${line}` : ""}</span>} onClose={onClose} width="max-w-5xl">
      <ErrorNote message={error} />
      {content === null && !error && <Spinner label="Loading code…" />}
      {content !== null && <div className="h-[65vh] overflow-auto border border-line"><CodeView content={content} highlight={line ? [line, line] : null} scrollTo={line || null} /></div>}
    </Modal>
  );
}

type Choice = { targets: Set<string>; external: boolean };

/**
 * Confirmation popup for one caller: its candidates with evidence, an explanation (rules, or the LLM on request), and the
 * choices: confirm one or several targets, mark as external, or reject. The decision is stored and re-applied on relink.
 */
export function ReviewDialog({ landscapeId, group, canWrite, onClose, onDecided }: {
  landscapeId: string; group: ReviewGroup; canWrite: boolean; onClose: () => void; onDecided: () => void;
}) {
  const internal = group.candidates.filter((c) => c.target_repo_id);
  const initial = new Set(
    group.decision?.targets ??
      internal.filter((c) => c.status === "confirmed" || c.status === "auto").map((c) => `${c.target_repo_id}|${c.target_key}`),
  );
  const [choice, setChoice] = useState<Choice>({ targets: initial, external: !!group.decision?.external });
  const [externalName, setExternalName] = useState(group.decision?.external_name || group.candidates.find((c) => c.external_name)?.external_name || "");
  const [note, setNote] = useState(group.decision?.note ?? "");
  const [explanation, setExplanation] = useState<{ text: string; source: string } | null>(null);
  const [explaining, setExplaining] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [code, setCode] = useState<{ analysisId: string; path: string; line: number; title: string } | null>(null);

  async function explain() {
    setExplaining(true);
    setError("");
    try {
      const data = await post<{ explanation: string; source: string }>(`/api/landscapes/${landscapeId}/explain`, { group_key: group.group_key });
      setExplanation({ text: data.explanation, source: data.source });
    } catch (err) {
      setError(errorText(err));
    } finally {
      setExplaining(false);
    }
  }

  useEffect(() => {
    void explain();
  }, []);

  async function decide(body: Record<string, unknown>) {
    setBusy(true);
    setError("");
    try {
      await post(`/api/landscapes/${landscapeId}/decisions`, { group_key: group.group_key, note, ...body });
      onDecided();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  const toggle = (key: string) => {
    const next = new Set(choice.targets);
    if (next.has(key)) next.delete(key);
    else next.add(key);
    setChoice({ ...choice, targets: next });
  };
  const source = group.candidates[0];

  return (
    <Modal
      title={<span className="flex items-center gap-2"><KindChip kind={group.kind} />Review link{group.ambiguous && <span className="chip border-warning/50 text-warning">ambiguous</span>}</span>}
      onClose={onClose}
      width="max-w-5xl"
      footer={
        canWrite ? (
          <>
            {group.decision && <button type="button" className="btn mr-auto" disabled={busy} onClick={() => void decide({ action: "reset" })}>Undo decision</button>}
            <button type="button" className="btn" onClick={onClose}>Cancel</button>
            <button type="button" className="btn btn-danger" disabled={busy} onClick={() => void decide({ reject: true, targets: [] })}>Not a link</button>
            <button type="button" className="btn btn-primary" disabled={busy || (!choice.targets.size && !choice.external)}
              onClick={() => void decide({ targets: [...choice.targets], external: choice.external, external_name: externalName })}>
              {busy ? "Saving…" : "Confirm"}
            </button>
          </>
        ) : <button type="button" className="btn" onClick={onClose}>Close</button>
      }
    >
      <div className="grid gap-4 lg:grid-cols-[1fr_340px]">
        <div className="min-w-0 space-y-3">
          <section className="border border-line p-3">
            <div className="label-micro">Caller</div>
            <div className="mt-1 font-mono text-code"><span className="text-primary">{group.source_repo_name}</span> · {group.source_name}</div>
            <div className="mt-1 flex flex-wrap items-center gap-2 text-body-sm text-muted">
              <span className="font-mono">{group.label}</span>
              {group.source_path && (
                <button type="button" className="link font-mono" onClick={() => source && setCode({ analysisId: source.source_analysis_id, path: group.source_path, line: group.source_line, title: group.source_repo_name })}>
                  {group.source_path}:{group.source_line}
                </button>
              )}
              {group.features.map((f) => <span key={f} className="chip border-primary/40 text-primary">{f}</span>)}
            </div>
          </section>

          <div className="label-micro">Candidates · tick every repository this call really reaches (a gateway can fan out)</div>
          {internal.map((c) => {
            const key = `${c.target_repo_id}|${c.target_key}`;
            return (
              <label key={key} className={`block cursor-pointer border p-3 ${choice.targets.has(key) ? "border-primary bg-primary/5" : "border-line"}`}>
                <div className="flex flex-wrap items-center gap-2">
                  <input type="checkbox" disabled={!canWrite} checked={choice.targets.has(key)} onChange={() => toggle(key)} />
                  <span className="font-mono text-code"><span className="text-primary">{c.target_repo_name}</span> · {c.target_name}</span>
                  <ConfidencePill link={c} />
                  <StatusPill status={c.status} />
                  {c.target_path && (
                    <button type="button" className="link ml-auto font-mono text-micro" onClick={(e) => { e.preventDefault(); setCode({ analysisId: c.target_analysis_id, path: c.target_path, line: c.target_line, title: c.target_repo_name }); }}>
                      {c.target_path}:{c.target_line}
                    </button>
                  )}
                </div>
                <ul className="mt-2 list-disc space-y-0.5 pl-8 text-body-sm text-muted">
                  {c.evidence.map((e, i) => <li key={i}>{e}</li>)}
                </ul>
              </label>
            );
          })}
          {!internal.length && <p className="text-body-md text-muted">No repository in the landscape provides this interface.</p>}
          <label className={`flex flex-wrap items-center gap-2 border p-3 ${choice.external ? "border-primary bg-primary/5" : "border-line"}`}>
            <input type="checkbox" disabled={!canWrite} checked={choice.external} onChange={(e) => setChoice({ ...choice, external: e.target.checked })} />
            <span className="text-body-md">It goes to a system outside this landscape</span>
            <input className="field h-7 w-64" placeholder="e.g. Partner API, Mainframe gateway" value={externalName} disabled={!choice.external || !canWrite} onChange={(e) => setExternalName(e.target.value)} />
          </label>
          {canWrite && <label className="block"><span className="label-micro">Note (optional)</span><input className="field mt-1" value={note} onChange={(e) => setNote(e.target.value)} placeholder="why — for the next reviewer" /></label>}
          <ErrorNote message={error} />
        </div>
        <aside className="border border-line bg-inset p-3">
          <div className="flex items-center justify-between">
            <span className="label-micro">Explanation</span>
            {explanation && <span className="chip border-line text-faint">{explanation.source === "llm" || explanation.source === "cache" ? "AI" : "rules"}</span>}
          </div>
          {explaining && <div className="mt-2"><Spinner label="Thinking…" /></div>}
          {explanation && <Markdown className="mt-2 text-body-md" text={explanation.text} />}
          {explanation?.source === "rules" && <p className="mt-3 text-body-sm text-faint">Configure an LLM under Admin › LLM for an AI explanation.</p>}
          {group.decision && (
            <p className="mt-3 border-t border-line pt-2 text-body-sm text-muted">
              Decided {group.decision.reject ? "as not a link" : group.decision.external ? "as external" : "for the ticked target(s)"}{group.decision.note ? ` — “${group.decision.note}”` : ""}.
            </p>
          )}
        </aside>
      </div>
      {code && <CodeModal {...code} onClose={() => setCode(null)} />}
    </Modal>
  );
}
