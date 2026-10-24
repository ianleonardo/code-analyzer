import { FormEvent, useEffect, useState } from "react";
import { api, del, errorText, formatDate, formatDateTime, patch, post } from "../../api";
import { useAuth } from "../../auth";
import { ConfirmDialog, ErrorNote, Field, Modal, PageHeader, Select } from "../../components/ui";
import { AREAS, can } from "../../permissions";
import type { RoleRecord, User } from "../../types";

const LOGIN_ID = /^[A-Za-z0-9]{3,64}$/;
const DEFAULT_LDAP_DOMAINS = ["NTSGPDOM", "NTMYPDOM"];

function UserDialog({ user, roles, ldapDomains, onSaved, onClose }: { user: User | null; roles: RoleRecord[]; ldapDomains: string[]; onSaved: () => void; onClose: () => void }) {
  const editing = user !== null;
  const domains = ldapDomains.length ? ldapDomains : DEFAULT_LDAP_DOMAINS;
  const [loginId, setLoginId] = useState(user?.login_id ?? "");
  const [name, setName] = useState(user?.username ?? "");
  const [password, setPassword] = useState("");
  const [role, setRole] = useState(user?.role_id ?? "analyst");
  const [signIn, setSignIn] = useState<"local" | "ldap">(user?.auth_provider === "ldap" ? "ldap" : "local");
  const [ldapDomain, setLdapDomain] = useState(user?.ldap_domain || domains[0] || "NTSGPDOM");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const ldap = signIn === "ldap";

  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    if (!editing && !LOGIN_ID.test(loginId.trim())) return setError("Login ID must be 3-64 letters or numbers, with no spaces or special characters");
    if (!ldap && (!editing || password || user?.auth_provider === "ldap") && password.length < 8) return setError("Password must be at least 8 characters");
    setBusy(true);
    try {
      const body: Record<string, string> = { username: name.trim(), role_id: role, auth_provider: signIn };
      if (!editing) body.login_id = loginId.trim();
      if (ldap) body.ldap_domain = ldapDomain;
      if (!ldap && password) body.password = password;
      if (editing) await patch(`/api/users/${user.user_id}`, body);
      else await post("/api/users", body);
      onSaved();
    } catch (err) {
      setError(errorText(err));
      setBusy(false);
    }
  }

  const selected = roles.find((r) => r.role_id === role);
  return (
    <Modal title={editing ? "Edit user" : "Add user"} onClose={onClose} width="max-w-md">
      <form className="space-y-3" onSubmit={(e) => void submit(e)}>
        <Field label="Login ID" hint={!editing ? "Letters and numbers only, no spaces. Not case-sensitive." : undefined}>
          <input className="field" value={loginId} readOnly={editing} required autoFocus={!editing} onChange={(e) => setLoginId(e.target.value)} />
        </Field>
        <Field label="Name"><input className="field" value={name} required onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Sign-in">
          <Select className="field" value={signIn} onChange={(e) => { setSignIn(e.target.value as "local" | "ldap"); setPassword(""); }}>
            <option value="local">Local database</option>
            <option value="ldap">Active Directory (LDAP)</option>
          </Select>
        </Field>
        {ldap && (
          <Field label="LDAP domain" hint={`Bind as ${ldapDomain}\\${loginId || "<login_id>"}`}>
            <Select className="field" value={ldapDomain} onChange={(e) => setLdapDomain(e.target.value)}>
              {domains.map((dom) => (
                <option key={dom} value={dom}>{dom}</option>
              ))}
            </Select>
          </Field>
        )}
        <Field label="Password" hint={ldap ? "This user signs in with their Active Directory password. No password is stored here." : editing ? "Leave blank to keep the current password." : "Stored (bcrypt) in this application."}>
          <input className="field" type="password" value={ldap ? "" : password} disabled={ldap} autoComplete="new-password" onChange={(e) => setPassword(e.target.value)} />
        </Field>
        <Field label="Role" hint={selected ? AREAS.map((a) => `${a.label}: ${selected[a.key]}`).join(" · ") : undefined}>
          <Select className="field" value={role} onChange={(e) => setRole(e.target.value)}>
            {roles.map((r) => <option key={r.role_id} value={r.role_id}>{r.name}</option>)}
          </Select>
        </Field>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2">
          <button type="button" className="btn" onClick={onClose}>Cancel</button>
          <button type="submit" className="btn btn-primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button>
        </div>
      </form>
    </Modal>
  );
}

export function UsersPage() {
  const { user: me } = useAuth();
  const canWrite = can(me, "settings", "write");
  const [users, setUsers] = useState<User[]>([]);
  const [roles, setRoles] = useState<RoleRecord[]>([]);
  const [ldapDomains, setLdapDomains] = useState<string[]>(DEFAULT_LDAP_DOMAINS);
  const [editing, setEditing] = useState<User | null | "new">(null);
  const [deleting, setDeleting] = useState<User | null>(null);
  const [error, setError] = useState("");
  const [q, setQ] = useState("");

  const load = () => {
    api<User[]>("/api/users").then(setUsers).catch((err) => setError(errorText(err)));
    api<RoleRecord[]>("/api/roles").then(setRoles).catch(() => undefined);
    api<{ domains: string[] }>("/api/users/ldap-domains").then((res) => { if (res.domains?.length) setLdapDomains(res.domains); }).catch(() => undefined);
  };
  useEffect(load, []);
  const shown = users.filter((u) => `${u.username} ${u.login_id} ${u.role_name} ${u.ldap_domain ?? ""}`.toLowerCase().includes(q.toLowerCase()));

  return (
    <div>
      <PageHeader
        icon="group"
        title="Users"
        subtitle="Local or Active Directory sign-in; access comes from the user's role."
        right={
          <>
            <input className="field w-56" placeholder="Search users…" value={q} onChange={(e) => setQ(e.target.value)} />
            {canWrite && <button type="button" className="btn btn-primary" onClick={() => setEditing("new")}><span className="material-symbols-outlined icon-sm">person_add</span>Add user</button>}
          </>
        }
      />
      <div className="p-4">
        <ErrorNote message={error} />
        <div className="panel">
          <table className="w-full">
            <thead className="table-head"><tr><th>Name</th><th>Login ID</th><th>Sign-in</th><th>Role</th><th>Last sign-in</th><th>Created</th><th /></tr></thead>
            <tbody>
              {shown.map((u) => (
                <tr key={u.user_id} className="table-row">
                  <td>{u.username}</td>
                  <td className="font-mono text-code">{u.login_id}</td>
                  <td>
                    <span className="chip border-line text-muted">
                      {u.auth_provider === "ldap" ? `Active Directory${u.ldap_domain ? ` · ${u.ldap_domain}` : ""}` : "Local"}
                    </span>
                  </td>
                  <td><span className="chip border-primary/40 text-primary">{u.role_name}</span></td>
                  <td className="font-mono text-micro text-faint">{formatDateTime(u.last_login_at)}</td>
                  <td className="font-mono text-micro text-faint">{formatDate(u.created_at)}</td>
                  <td className="text-right">
                    {canWrite && (
                      <>
                        <button type="button" className="icon-btn" title="Edit" onClick={() => setEditing(u)}><span className="material-symbols-outlined icon-sm">edit</span></button>
                        {u.user_id !== me?.user_id && <button type="button" className="icon-btn hover:!text-critical" title="Delete" onClick={() => setDeleting(u)}><span className="material-symbols-outlined icon-sm">delete</span></button>}
                      </>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {editing && <UserDialog user={editing === "new" ? null : editing} roles={roles} ldapDomains={ldapDomains} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}
      {deleting && (
        <ConfirmDialog
          title="Delete user"
          message={`Delete ${deleting.username} (${deleting.login_id})? Their Git connections are removed too.`}
          onClose={() => setDeleting(null)}
          onConfirm={async () => {
            try {
              await del(`/api/users/${deleting.user_id}`);
            } catch (err) {
              setError(errorText(err));
            }
            setDeleting(null);
            load();
          }}
        />
      )}
    </div>
  );
}
