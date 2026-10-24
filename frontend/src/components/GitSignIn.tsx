import { useEffect, useMemo, useState } from "react";
import { ApiError, errorText, post } from "../api";
import type { GitCheck } from "../types";
import { ErrorNote, Field, Modal } from "./ui";

const LOGOS: Record<string, string> = { github: "hub", bitbucket: "cloud", bitbucket_server: "dns", git: "account_tree" };

/**
 * Sign-in popup for a Git host. With an OAuth app configured, "Continue with GitHub/Bitbucket" opens the provider's
 * login window and waits for it to post back; otherwise (and always for Bitbucket Server) the user signs in with a
 * personal access token / app password, which is verified against the repository before it is stored.
 */
export function GitSignInDialog({ check, url, onClose, onConnected }: { check: GitCheck; url: string; onClose: () => void; onConnected: () => void }) {
  const provider = check.repo.provider;
  const label = check.repo.provider_label;
  const [username, setUsername] = useState("");
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [waiting, setWaiting] = useState(false);
  const help = check.token_help;

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (event.origin !== window.location.origin || !event.data || event.data.type !== "git-auth") return;
      setWaiting(false);
      if (event.data.ok) onConnected();
      else setError(String(event.data.message || "Sign-in was not completed"));
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [onConnected]);

  const popupFeatures = useMemo(() => {
    const width = 620;
    const height = 720;
    const left = window.screenX + (window.outerWidth - width) / 2;
    const top = window.screenY + (window.outerHeight - height) / 2;
    return `popup=yes,width=${width},height=${height},left=${left},top=${top}`;
  }, []);

  async function oauth() {
    setError("");
    // open synchronously so the browser does not block the popup, then point it at the provider
    const popup = window.open("about:blank", "git-oauth", popupFeatures);
    try {
      const { authorize_url } = await post<{ authorize_url: string }>(`/api/git/oauth/${provider}/start`, { host: check.repo.host });
      if (!popup) {
        setError("The browser blocked the sign-in window. Allow pop-ups for this site and try again.");
        return;
      }
      popup.location.href = authorize_url;
      setWaiting(true);
      const timer = setInterval(() => {
        if (popup.closed) {
          clearInterval(timer);
          setWaiting(false);
        }
      }, 600);
    } catch (err) {
      popup?.close();
      setError(errorText(err));
    }
  }

  async function saveToken() {
    setBusy(true);
    setError("");
    try {
      await post("/api/git/credentials", { url, username, token });
      onConnected();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sign in");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Modal
      title={
        <span className="flex items-center gap-2">
          <span className="material-symbols-outlined text-primary">{LOGOS[provider] ?? "login"}</span>
          Sign in to {label} · <span className="font-mono text-muted">{check.repo.host}</span>
        </span>
      }
      onClose={onClose}
      width="max-w-md"
    >
      <div className="space-y-3">
        <p className="text-body-md text-muted">
          <span className="font-mono text-ink">{check.repo.name}</span> needs your {label} access. Your token is encrypted and used only for your own analyses; it is never written into a clone.
        </p>
        {check.oauth_available && (
          <>
            <button type="button" className="btn btn-primary h-9 w-full justify-center" onClick={() => void oauth()} disabled={waiting}>
              <span className="material-symbols-outlined">open_in_new</span>
              {waiting ? `Waiting for ${label} sign-in window…` : `Continue with ${label}`}
            </button>
            <div className="flex items-center gap-2 text-faint">
              <span className="h-px flex-1 bg-line" />
              <span className="label-micro">or use an access token</span>
              <span className="h-px flex-1 bg-line" />
            </div>
          </>
        )}
        <Field label={`${help.username_label ?? "Username"}${help.username_required ? "" : " (optional)"}`}>
          <input className="field" value={username} autoComplete="username" onChange={(e) => setUsername(e.target.value)} placeholder={provider === "bitbucket" ? "you@company.com" : "user id"} />
        </Field>
        <Field
          label={provider === "bitbucket" ? "API token / access token" : provider === "bitbucket_server" ? "HTTP access token" : "Personal access token"}
          hint={
            <>
              {help.hint}{" "}
              {help.url && (
                <a className="link" href={help.url} target="_blank" rel="noreferrer">
                  Create one ↗
                </a>
              )}
            </>
          }
        >
          <input className="field" type="password" value={token} autoComplete="new-password" onChange={(e) => setToken(e.target.value)} />
        </Field>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="button" className="btn btn-primary" disabled={busy || !token || (help.username_required && !username)} onClick={() => void saveToken()}>
            {busy ? "Verifying…" : "Sign in & verify access"}
          </button>
        </div>
      </div>
    </Modal>
  );
}
