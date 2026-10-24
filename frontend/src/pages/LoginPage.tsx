import { FormEvent, useState } from "react";
import { Navigate } from "react-router-dom";
import { errorText } from "../api";
import { useAuth } from "../auth";
import { LoginBackground } from "../components/LoginBackground";
import { ErrorNote } from "../components/ui";
import { useTheme } from "../theme";

export function LoginPage() {
  const { user, login } = useAuth();
  const { theme, toggle } = useTheme();
  const [loginId, setLoginId] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  if (user) return <Navigate to="/" replace />;

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      await login(loginId, password);
    } catch (err) {
      setError(errorText(err, "Sign in failed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid-canvas relative flex min-h-screen items-center justify-center overflow-hidden px-4">
      <LoginBackground theme={theme} />
      <button type="button" className="icon-btn absolute right-4 top-4 z-20 h-8 w-8 border border-line bg-surface/80 backdrop-blur-sm" onClick={toggle} title="Toggle Dark Telemetry / Light Laboratory">
        <span className="material-symbols-outlined">{theme === "dark" ? "light_mode" : "dark_mode"}</span>
      </button>
      <div className="relative z-10 w-full max-w-[880px] grid md:grid-cols-[1.1fr_1fr] border border-line bg-surface/95 backdrop-blur-sm shadow-2xl">
        <div className="hidden border-r border-line p-8 md:block">
          <div className="flex items-center gap-2">
            <span className="material-symbols-outlined icon-lg text-primary">terminal</span>
            <span className="font-mono text-head-md tracking-tight">CODE ANALYZER</span>
          </div>
          <p className="mt-1 font-mono text-micro text-faint">:: HYBRID DETERMINISTIC-SEMANTIC REPOSITORY INTELLIGENCE</p>
          <div className="mt-8 space-y-3 font-mono text-code">
            {[
              ["01", "Ingestion", "GitHub · Bitbucket · Git · archive · folder, per branch"],
              ["02", "Macro discovery", "Manifests, frameworks, containers, docs drift"],
              ["03", "Code graph", "Symbols, imports, calls, Tarjan SCC, PageRank"],
              ["04", "Synthesis", "C4 diagrams, file roles, summaries"],
              ["05", "Chat & compare", "Ask the analyst offline; diff repos and branches"],
            ].map(([n, title, text]) => (
              <div key={n} className="flex gap-3">
                <span className="text-primary">{n}</span>
                <div>
                  <div className="text-ink">{title}</div>
                  <div className="text-micro text-faint normal-case">{text}</div>
                </div>
              </div>
            ))}
          </div>
          <div className="mt-8 h-1.5 w-full bg-elevated">
            <div className="h-full w-2/3 bg-primary stripes" />
          </div>
        </div>
        <form className="p-8" onSubmit={(e) => void submit(e)}>
          <h1 className="text-head-lg">Sign in</h1>
          <label className="mt-6 block">
            <span className="label-micro">Login ID</span>
            <input className="field mt-1 h-9" autoComplete="username" required value={loginId} onChange={(e) => setLoginId(e.target.value)} aria-label="Login ID" />
          </label>
          <label className="mt-3 block">
            <span className="label-micro">Password</span>
            <input className="field mt-1 h-9" type="password" autoComplete="current-password" required value={password} onChange={(e) => setPassword(e.target.value)} aria-label="Password" />
          </label>
          <div className="mt-3">
            <ErrorNote message={error} />
          </div>
          <button className="btn btn-primary mt-4 h-9 w-full justify-center" type="submit" disabled={busy}>
            <span className="material-symbols-outlined icon-sm">login</span>
            {busy ? "Signing in…" : "Sign in"}
          </button>
        </form>
      </div>
    </div>
  );
}
