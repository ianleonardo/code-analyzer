import { useState } from "react";
import { errorText, post } from "../api";
import { useAuth } from "../auth";
import { ErrorNote, Field, PageHeader } from "../components/ui";

export function AccountPage() {
  const { user } = useAuth();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [error, setError] = useState("");
  const [done, setDone] = useState(false);
  return (
    <div>
      <PageHeader icon="password" title="Change password" subtitle={`${user?.username} · ${user?.login_id}`} />
      <div className="max-w-md space-y-3 p-4">
        {user?.auth_provider === "ldap" ? (
          <p className="text-muted">You sign in with Active Directory; change your password there.</p>
        ) : (
          <>
            <Field label="Current password"><input className="field" type="password" value={current} onChange={(e) => setCurrent(e.target.value)} /></Field>
            <Field label="New password" hint="At least 8 characters."><input className="field" type="password" value={next} onChange={(e) => setNext(e.target.value)} /></Field>
            <ErrorNote message={error} />
            {done && <p className="text-ok">Password changed.</p>}
            <button type="button" className="btn btn-primary" disabled={!current || next.length < 8} onClick={async () => { setError(""); setDone(false); try { await post("/api/auth/password", { current_password: current, new_password: next }); setDone(true); setCurrent(""); setNext(""); } catch (err) { setError(errorText(err)); } }}>Change password</button>
          </>
        )}
      </div>
    </div>
  );
}
