import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, del, errorText, patch, post, timeAgo } from "../api";
import type { ChatSession } from "../types";
import { ConfirmDialog, ErrorNote, Markdown, Spinner, SplitPane } from "./ui";

const STARTERS: Record<string, string[]> = {
  global: [
    "How do services communicate with each other (APIs, messaging, databases)?",
    "Which repositories publish or consume event messages (Kafka, MQ)?",
    "List all external databases and third-party APIs used across repositories.",
    "Summarise the architecture style and business purpose of each repository.",
    "Which repositories have the highest technical debt or architectural risks?",
  ],
  landscape: [
    "Explain the end-to-end data flow between services in this landscape.",
    "Which cross-repository APIs and messaging topics connect these services?",
    "Are there shared databases or tight couplings between repositories in this landscape?",
    "What are the main architectural risks across these member repositories?",
  ],
  analysis: [
    "What is the business purpose of this repository and its main capabilities?",
    "Walk me through the request flow from an API endpoint to the database.",
    "Which modules are the riskiest to change, and why?",
    "Explain the role of the most central file and who depends on it.",
    "Where is authentication and authorisation implemented?",
    "Summarise the tech stack and external systems this code talks to.",
  ],
  comparison: [
    "Summarise the most important differences between the two sides.",
    "Which side has the healthier architecture, and what drives the difference?",
    "What changed in dependencies and could any change be breaking?",
    "Which new concerns were introduced on the right side?",
  ],
};

/** Conversations grounded in a stored analysis, comparison, or global portfolio. */
export function ChatPanel({ scopeType, scopeId, compact = false }: { scopeType: "analysis" | "comparison" | "global" | "landscape"; scopeId: string; compact?: boolean }) {
  const navigate = useNavigate();
  const [sessions, setSessions] = useState<ChatSession[]>([]);
  const [current, setCurrent] = useState<ChatSession | null>(null);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [deleting, setDeleting] = useState<ChatSession | null>(null);
  const [renaming, setRenaming] = useState<string | null>(null);
  const [copiedId, setCopiedId] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  async function copyMessage(messageId: string, content: string) {
    try {
      if (navigator.clipboard?.writeText) {
        await navigator.clipboard.writeText(content);
      } else {
        const area = document.createElement("textarea");
        area.value = content;
        area.style.position = "fixed";
        area.style.opacity = "0";
        document.body.appendChild(area);
        area.select();
        document.execCommand("copy");
        document.body.removeChild(area);
      }
      setCopiedId(messageId);
      window.setTimeout(() => setCopiedId((cur) => (cur === messageId ? null : cur)), 2000);
    } catch {
      // ignore clipboard errors
    }
  }

  const load = useCallback(async (select?: string) => {
    const list = await api<ChatSession[]>(`/api/chat/sessions?scope_type=${scopeType}&scope_id=${scopeId}`);
    setSessions(list);
    const target = select ?? list[0]?.session_id;
    if (target) setCurrent(await api<ChatSession>(`/api/chat/sessions/${target}`));
    else setCurrent(null);
  }, [scopeType, scopeId]);

  useEffect(() => {
    load().catch((err) => setError(errorText(err)));
  }, [load]);

  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [current?.messages?.length, busy]);

  async function newSession() {
    setError("");
    try {
      const session = await post<ChatSession>("/api/chat/sessions", { scope_type: scopeType, scope_id: scopeId });
      await load(session.session_id);
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function toggleVisibility(session: ChatSession) {
    setError("");
    try {
      const updated = await patch<ChatSession>(`/api/chat/sessions/${session.session_id}`, {
        is_public: !session.is_public,
      });
      if (current?.session_id === session.session_id) {
        setCurrent(updated);
      }
      setSessions(await api<ChatSession[]>(`/api/chat/sessions?scope_type=${scopeType}&scope_id=${scopeId}`));
    } catch (err) {
      setError(errorText(err));
    }
  }

  async function send(question: string) {
    if (!question.trim() || busy) return;
    setError("");
    setBusy(true);
    let session = current;
    try {
      if (!session || session.is_owner === false) {
        session = await post<ChatSession>("/api/chat/sessions", { scope_type: scopeType, scope_id: scopeId });
      }
      const pending: ChatSession = { ...session, messages: [...(session.messages ?? []), { message_id: "pending", role: "user", content: question, sources: [], created_at: new Date().toISOString() }] };
      setCurrent(pending);
      setText("");
      const answered = await post<ChatSession>(`/api/chat/sessions/${session.session_id}/messages`, { content: question });
      setCurrent(answered);
      setSessions(await api<ChatSession[]>(`/api/chat/sessions?scope_type=${scopeType}&scope_id=${scopeId}`));
    } catch (err) {
      setError(errorText(err));
      if (session) setCurrent(await api<ChatSession>(`/api/chat/sessions/${session.session_id}`).catch(() => session));
    } finally {
      setBusy(false);
    }
  }

  const sidebar = (
    <aside className="flex h-full w-full flex-col bg-surface">
      <div className="panel-head pr-9">
        <span>Conversations</span>
        <button type="button" className="icon-btn" title="New private conversation" onClick={() => void newSession()}>
          <span className="material-symbols-outlined">add</span>
        </button>
      </div>
      <div className="flex-1 overflow-auto">
        {sessions.map((session) => {
          const isOwner = session.is_owner !== false;
          return (
            <div key={session.session_id} className={`group flex items-center gap-1 border-b border-line/60 px-2 py-1.5 ${current?.session_id === session.session_id ? "bg-elevated border-l-2 border-l-primary" : "hover:bg-elevated/50"}`}>
              {renaming === session.session_id ? (
                <input
                  autoFocus
                  className="field h-6"
                  defaultValue={session.title}
                  onBlur={async (e) => {
                    setRenaming(null);
                    await patch(`/api/chat/sessions/${session.session_id}`, { title: e.target.value });
                    void load(current?.session_id);
                  }}
                  onKeyDown={(e) => e.key === "Enter" && (e.target as HTMLInputElement).blur()}
                />
              ) : (
                <button type="button" className="min-w-0 flex-1 text-left" onClick={async () => setCurrent(await api<ChatSession>(`/api/chat/sessions/${session.session_id}`))}>
                  <span className="flex items-center gap-1 text-body-md">
                    <span
                      className={`material-symbols-outlined icon-sm shrink-0 ${session.is_public ? "text-primary" : "text-faint"}`}
                      title={session.is_public ? "Public conversation (visible to all users)" : "Private conversation (only you)"}
                    >
                      {session.is_public ? "public" : "lock"}
                    </span>
                    <span className="truncate">{session.title}</span>
                  </span>
                  <span className="font-mono text-micro text-faint">
                    {session.is_public ? "Public" : "Private"}
                    {!isOwner && session.owner_name ? ` · by ${session.owner_name}` : ""}
                    {" · "}
                    {session.message_count ?? 0} msgs · {timeAgo(session.updated_at)}
                  </span>
                </button>
              )}
              {isOwner && (
                <>
                  <button
                    type="button"
                    className={`icon-btn opacity-0 group-hover:opacity-100 ${session.is_public ? "text-primary" : ""}`}
                    title={session.is_public ? "Public — click to make private" : "Private — click to make public"}
                    onClick={() => void toggleVisibility(session)}
                  >
                    <span className="material-symbols-outlined icon-sm">{session.is_public ? "public" : "lock"}</span>
                  </button>
                  <button type="button" className="icon-btn opacity-0 group-hover:opacity-100" title="Rename" onClick={() => setRenaming(session.session_id)}>
                    <span className="material-symbols-outlined icon-sm">edit</span>
                  </button>
                  <button type="button" className="icon-btn opacity-0 group-hover:opacity-100 hover:!text-critical" title="Delete" onClick={() => setDeleting(session)}>
                    <span className="material-symbols-outlined icon-sm">delete</span>
                  </button>
                </>
              )}
            </div>
          );
        })}
        {!sessions.length && <p className="p-3 text-body-md text-muted">No conversations yet. Ask a question to start a private chat.</p>}
      </div>
    </aside>
  );

  const isReadOnlyPublic = Boolean(current && current.is_owner === false);

  const chatBody = (
    <section className="flex h-full min-w-0 flex-1 flex-col">
      {current && (
        <div className="flex h-7 shrink-0 items-center justify-between gap-2 border-b border-line bg-surface px-3 pr-9 text-micro">
          <div className="flex min-w-0 items-center gap-1.5">
            <span className={`material-symbols-outlined icon-sm ${current.is_public ? "text-primary" : "text-muted"}`}>
              {current.is_public ? "public" : "lock"}
            </span>
            <span className="truncate font-semibold text-ink">{current.title}</span>
            {current.is_owner === false && current.owner_name && (
              <span className="chip h-4 border-line text-muted normal-case tracking-normal">Shared by {current.owner_name}</span>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {current.is_owner !== false ? (
              <>
                <button
                  type="button"
                  className={`inline-flex items-center gap-1 border px-2 py-0.5 font-mono text-micro transition-colors ${
                    current.is_public
                      ? "border-primary/50 bg-primary/10 text-primary hover:bg-primary/20"
                      : "border-line bg-elevated text-muted hover:border-line-strong hover:text-ink"
                  }`}
                  title={
                    current.is_public
                      ? "This chat is public and visible to other users. Click to make it private."
                      : "This chat is private to you. Click to mark as public so other users can view it."
                  }
                  onClick={() => void toggleVisibility(current)}
                >
                  <span className="material-symbols-outlined icon-sm">{current.is_public ? "public" : "lock"}</span>
                  <span>{current.is_public ? "Public · Shared" : "Private · Only you"}</span>
                </button>
                <button
                  type="button"
                  className="icon-btn hover:!text-critical"
                  title="Delete conversation"
                  onClick={() => setDeleting(current)}
                >
                  <span className="material-symbols-outlined icon-sm">delete</span>
                </button>
              </>
            ) : (
              <span className="font-mono text-micro text-muted">Public (View only)</span>
            )}
          </div>
        </div>
      )}
      <div className="flex-1 overflow-auto px-4 pr-9 py-3">
        {current?.llm_configured === false && (
          <p className="mb-3 border border-warning/40 bg-warning/10 px-2 py-1.5 text-body-md text-warning">
            No LLM is configured — answers list the most relevant code found by retrieval. An administrator can add Gemini or Spirare under Admin › LLM.
          </p>
        )}
        {!(current?.messages?.length) && (
          <div className="mx-auto max-w-2xl py-6">
            <div className="mb-3 flex items-center gap-2">
              <span className="material-symbols-outlined icon-lg text-primary">psychology</span>
              <div>
                <p className="text-head-md">{scopeType === "global" ? "Global Cross-Repository Analyst" : scopeType === "landscape" ? "Landscape Analyst" : "Chat with Analyst"}</p>
                <p className="text-body-md text-muted">
                  {scopeType === "global"
                    ? "Ask questions across all repositories, services, APIs, event messaging, and system architectures in the organization."
                    : scopeType === "landscape"
                    ? "Ask questions about the services, shared databases, and integration flows in this landscape."
                    : "Answers come from the stored analysis — structure, file roles, graph and code — so they work offline and cite files."}
                </p>
              </div>
            </div>
            <div className="grid gap-2 sm:grid-cols-2">
              {(STARTERS[scopeType] ?? STARTERS.analysis).map((q) => (
                <button key={q} type="button" className="border border-line bg-surface px-3 py-2 text-left text-body-md hover:border-primary" onClick={() => void send(q)}>
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}
        <div className="mx-auto max-w-4xl space-y-4">
          {current?.messages?.map((message) => (
            <div key={message.message_id} className={message.role === "user" ? "flex justify-end" : ""}>
              {message.role === "user" ? (
                <div className="max-w-[80%] border border-primary/30 bg-primary/10 px-3 py-2 text-body-lg whitespace-pre-wrap">{message.content}</div>
              ) : (
                <div className="border border-line bg-surface">
                  <div className="flex h-7 items-center justify-between border-b border-line px-3 font-mono text-micro text-faint">
                    <span className="flex items-center gap-2">
                      <span className="material-symbols-outlined icon-sm text-primary">psychology</span> ANALYST
                    </span>
                    <button
                      type="button"
                      className={`inline-flex items-center gap-1 px-1.5 py-0.5 font-mono text-micro transition-colors ${
                        copiedId === message.message_id ? "text-ok" : "text-muted hover:text-ink"
                      }`}
                      title="Copy response to clipboard"
                      onClick={() => void copyMessage(message.message_id, message.content)}
                    >
                      <span className="material-symbols-outlined icon-sm">
                        {copiedId === message.message_id ? "check" : "content_copy"}
                      </span>
                      <span>{copiedId === message.message_id ? "Copied" : "Copy"}</span>
                    </button>
                  </div>
                  <Markdown text={message.content} className="px-3 py-2" />
                  {message.sources.length > 0 && (
                    <div className="flex flex-wrap gap-1 border-t border-line px-3 py-2">
                      <span className="label-micro mr-1 leading-5">Sources</span>
                      {message.sources.map((source, index) => (
                        <button
                          key={index}
                          type="button"
                          title={`${source.repo_name ? `[${source.repo_name}] ` : ""}${source.path} :: ${source.scope}`}
                          className="chip h-5 border-line-strong text-muted normal-case tracking-normal hover:border-primary hover:text-primary"
                          onClick={() => navigate(`/analyses/${source.analysis_id}?tab=inspector&file=${encodeURIComponent(source.path)}${source.line_start ? `&line=${source.line_start}` : ""}`)}
                        >
                          {source.repo_name && <span className="font-semibold text-primary/80 mr-0.5">[{source.repo_name}]</span>}
                          {source.side && <span className="text-primary">{source.side === "left" ? "L" : "R"}</span>}
                          {source.path.split("/").pop()}
                          {source.scope && source.scope !== "(file)" && <span className="text-faint">::{source.scope.split(".").pop()}</span>}
                          {source.line_start > 0 && <span className="text-faint">:{source.line_start}</span>}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          ))}
          {busy && <Spinner label="Retrieving code and reasoning over the analysis…" />}
          <div ref={bottom} />
        </div>
      </div>
      <div className="border-t border-line bg-surface p-3">
        <ErrorNote message={error} />
        {isReadOnlyPublic && (
          <div className="mx-auto mb-2 flex max-w-4xl items-center justify-between border border-line bg-elevated px-2.5 py-1 text-micro text-muted">
            <span>
              Viewing a public conversation shared by <strong className="text-ink">{current?.owner_name || "another user"}</strong> (view-only). Sending a question will start a new private conversation for you.
            </span>
            <button type="button" className="btn h-6 px-2 text-micro" onClick={() => void newSession()}>
              <span className="material-symbols-outlined icon-sm">add</span>New private chat
            </button>
          </div>
        )}
        <div className="mx-auto flex max-w-4xl items-end gap-2">
          <textarea
            className="field min-h-[44px] flex-1 resize-none font-sans text-body-lg"
            rows={2}
            placeholder={
              scopeType === "global"
                ? "Ask across all repositories, services, APIs, event messaging, and architecture… (Enter to send, Shift+Enter for a new line)"
                : scopeType === "landscape"
                ? "Ask about services, data flows, and integrations in this landscape… (Enter to send, Shift+Enter for a new line)"
                : scopeType === "comparison"
                ? "Ask about differences between the two snapshots… (Enter to send, Shift+Enter for a new line)"
                : "Ask only about this repository's code, architecture, flows, and functionalities… (Enter to send, Shift+Enter for a new line)"
            }
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter" && !e.shiftKey) {
                e.preventDefault();
                void send(text);
              }
            }}
          />
          <button type="button" className="btn btn-primary h-11" disabled={busy || !text.trim()} onClick={() => void send(text)}>
            <span className="material-symbols-outlined">send</span>
          </button>
        </div>
      </div>
    </section>
  );

  return (
    <div className="flex h-full min-h-0">
      {!compact ? (
        <SplitPane
          direction="horizontal"
          primary="first"
          initialSize={240}
          minSize={160}
          maxSize={480}
          firstTitle="Conversations"
          secondTitle="Chat with Analyst"
          first={sidebar}
          second={chatBody}
          className="w-full"
        />
      ) : (
        chatBody
      )}
      {deleting && (
        <ConfirmDialog
          title="Delete conversation"
          message={<>Delete “{deleting.title}” and its messages?</>}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            await del(`/api/chat/sessions/${deleting.session_id}`);
            setDeleting(null);
            void load();
          }}
        />
      )}
    </div>
  );
}
