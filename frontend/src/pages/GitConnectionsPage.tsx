import { useEffect, useState } from "react";
import { del, errorText, formatDateTime, post, api } from "../api";
import { ConfirmDialog, Empty, ErrorNote, Field, PageHeader, Select, SourceBadge } from "../components/ui";
import type { GitCredential } from "../types";

const PROVIDERS = [
  { key: "github", label: "GitHub", host: "github.com", hint: "Personal access token with repo scope (classic) or read-only Contents (fine-grained)." },
  { key: "bitbucket", label: "Bitbucket Cloud", host: "bitbucket.org", hint: "Atlassian API token (scopes read:repository:bitbucket, read:user:bitbucket) with your Atlassian account email as username — or a repository/workspace access token with the username left blank." },
  { key: "bitbucket_server", label: "Bitbucket Server / Data Center", host: "", hint: "HTTP access token with Repository read. Username optional." },
  { key: "git", label: "Other Git host", host: "", hint: "Username and access token or password for HTTPS git." },
];

/** Each user's own Git host connections, used to check access and clone private repositories. */
export function GitConnectionsPage() {
  const [items, setItems] = useState<GitCredential[]>([]);
  const [oauth, setOauth] = useState<Record<string, boolean>>({});
  const [provider, setProvider] = useState("github");
  const [host, setHost] = useState("github.com");
  const [username, setUsername] = useState("");
  const [token, setToken] = useState("");
  const [error, setError] = useState("");
  const [message, setMessage] = useState("");
  const [busy, setBusy] = useState(false);
  const [removing, setRemoving] = useState<GitCredential | null>(null);

  const load = () => api<{ credentials: GitCredential[]; oauth: Record<string, boolean> }>("/api/git/credentials").then((data) => { setItems(data.credentials); setOauth(data.oauth); });
  useEffect(() => {
    void load();
    const onMessage = (event: MessageEvent) => {
      if (event.origin === window.location.origin && event.data?.type === "git-auth") {
        setMessage(event.data.ok ? `Connected as ${event.data.account}` : String(event.data.message));
        void load();
      }
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, []);

  const meta = PROVIDERS.find((p) => p.key === provider)!;
  async function save() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const saved = await post<GitCredential>("/api/git/credentials", { provider, host, username, token, base_url: host ? `https://${host}` : "" });
      setMessage(`Connected as ${saved.account_name || saved.username}`);
      setToken("");
      void load();
    } catch (err) {
      setError(errorText(err));
    } finally {
      setBusy(false);
    }
  }
  async function connectOAuth(key: string) {
    const popup = window.open("about:blank", "git-oauth", "popup=yes,width=620,height=720");
    try {
      const { authorize_url } = await post<{ authorize_url: string }>(`/api/git/oauth/${key}/start`, {});
      if (popup) popup.location.href = authorize_url;
    } catch (err) {
      popup?.close();
      setError(errorText(err));
    }
  }

  return (
    <div>
      <PageHeader icon="key" title="Git connections" subtitle="Your own sign-ins to GitHub and Bitbucket. Tokens are encrypted, verified before saving, and used only for your analyses." />
      <div className="grid gap-4 p-4 xl:grid-cols-[1fr_420px]">
        <section className="panel">
          <div className="panel-head"><span>Connected accounts</span></div>
          <table className="w-full">
            <thead className="table-head"><tr><th>Provider</th><th>Host</th><th>Account</th><th>Method</th><th>Verified</th><th /></tr></thead>
            <tbody>
              {items.map((c) => (
                <tr key={c.credential_id} className="table-row">
                  <td><SourceBadge source={c.provider} label={c.provider_label} /></td>
                  <td className="font-mono text-code">{c.host}</td>
                  <td className="font-mono text-code text-primary">{c.account_name || c.username || "—"}</td>
                  <td><span className="chip border-line text-muted">{c.auth_method}</span></td>
                  <td className="font-mono text-micro text-faint">{formatDateTime(c.last_verified_at)}</td>
                  <td className="text-right">
                    <button type="button" className="icon-btn" title="Verify" onClick={async () => { try { await post(`/api/git/credentials/${c.credential_id}/verify`); setMessage("Connection verified"); void load(); } catch (err) { setError(errorText(err)); } }}>
                      <span className="material-symbols-outlined icon-sm">verified</span>
                    </button>
                    <button type="button" className="icon-btn hover:!text-critical" title="Remove" onClick={() => setRemoving(c)}>
                      <span className="material-symbols-outlined icon-sm">link_off</span>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {!items.length && <Empty icon="key" title="No connections">Public repositories work without one. Add a connection for private repositories — or sign in from the access check when you add a repository.</Empty>}
        </section>
        <section className="panel p-3 space-y-3">
          <div className="label-micro">Add a connection</div>
          {(oauth.github || oauth.bitbucket) && (
            <div className="flex gap-2">
              {oauth.github && <button type="button" className="btn flex-1 justify-center" onClick={() => void connectOAuth("github")}><span className="material-symbols-outlined icon-sm">open_in_new</span>Sign in with GitHub</button>}
              {oauth.bitbucket && <button type="button" className="btn flex-1 justify-center" onClick={() => void connectOAuth("bitbucket")}><span className="material-symbols-outlined icon-sm">open_in_new</span>Sign in with Bitbucket</button>}
            </div>
          )}
          <Field label="Provider">
            <Select className="field" value={provider} onChange={(e) => { setProvider(e.target.value); setHost(PROVIDERS.find((p) => p.key === e.target.value)?.host ?? ""); }}>
              {PROVIDERS.map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
            </Select>
          </Field>
          <Field label="Host"><input className="field" value={host} placeholder="bitbucket.mycompany.com" onChange={(e) => setHost(e.target.value)} /></Field>
          <Field label={provider === "bitbucket" ? "Atlassian account email (blank for an access token)" : "Username"}><input className="field" value={username} onChange={(e) => setUsername(e.target.value)} /></Field>
          <Field label="Token" hint={meta.hint}><input className="field" type="password" autoComplete="new-password" value={token} onChange={(e) => setToken(e.target.value)} /></Field>
          <ErrorNote message={error} />
          {message && <p className="text-ok text-body-md">{message}</p>}
          <button type="button" className="btn btn-primary w-full justify-center" disabled={busy || !token || !host} onClick={() => void save()}>{busy ? "Verifying…" : "Verify & save"}</button>
        </section>
      </div>
      {removing && <ConfirmDialog title="Remove connection" message={`Remove your ${removing.provider_label} connection for ${removing.host}?`} confirmLabel="Remove" onClose={() => setRemoving(null)} onConfirm={async () => { await del(`/api/git/credentials/${removing.credential_id}`); setRemoving(null); void load(); }} />}
    </div>
  );
}
