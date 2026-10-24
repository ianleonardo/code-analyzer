import { FormEvent, useEffect, useState } from "react";
import { api, del, errorText, patch, post } from "../../api";
import { useAuth } from "../../auth";
import { ConfirmDialog, ErrorNote, Field, Modal, PageHeader } from "../../components/ui";
import { AREAS, can, type Level } from "../../permissions";
import type { RoleRecord } from "../../types";

const LEVEL_CLS: Record<Level, string> = { none: "border-line text-faint", read: "border-info/50 text-info", write: "border-ok/50 text-ok" };

function RoleDialog({ role, onSaved, onClose }: { role: RoleRecord | null; onSaved: () => void; onClose: () => void }) {
  const [name, setName] = useState(role?.name ?? "");
  const [description, setDescription] = useState(role?.description ?? "");
  const [levels, setLevels] = useState<Record<string, Level>>({ repositories: role?.repositories ?? "read", insights: role?.insights ?? "read", settings: role?.settings ?? "none" });
  const [error, setError] = useState("");
  async function submit(event: FormEvent) {
    event.preventDefault();
    setError("");
    try {
      const body = { name, description, ...levels };
      if (role) await patch(`/api/roles/${role.role_id}`, body);
      else await post("/api/roles", body);
      onSaved();
    } catch (err) {
      setError(errorText(err));
    }
  }
  return (
    <Modal title={role ? "Edit role" : "Add role"} onClose={onClose} width="max-w-lg">
      <form className="space-y-3" onSubmit={(e) => void submit(e)}>
        <Field label="Role name"><input className="field" value={name} required maxLength={50} onChange={(e) => setName(e.target.value)} /></Field>
        <Field label="Description"><input className="field" value={description} maxLength={200} onChange={(e) => setDescription(e.target.value)} /></Field>
        <div className="border border-line">
          {AREAS.map((area) => (
            <div key={area.key} className="flex items-center justify-between gap-3 border-b border-line/60 px-3 py-2 last:border-0">
              <div><div className="font-mono text-code">{area.label}</div><div className="text-body-sm text-faint">{area.help}</div></div>
              <div className="flex border border-line">
                {(["none", "read", "write"] as Level[]).map((level) => (
                  <button key={level} type="button" className={`h-7 px-2.5 font-mono text-micro uppercase ${levels[area.key] === level ? "bg-primary/10 text-primary" : "text-muted"}`} onClick={() => setLevels((l) => ({ ...l, [area.key]: level }))}>{level}</button>
                ))}
              </div>
            </div>
          ))}
        </div>
        <ErrorNote message={error} />
        <div className="flex justify-end gap-2"><button type="button" className="btn" onClick={onClose}>Cancel</button><button type="submit" className="btn btn-primary">Save role</button></div>
      </form>
    </Modal>
  );
}

export function RolesPage() {
  const { user } = useAuth();
  const canWrite = can(user, "settings", "write");
  const [roles, setRoles] = useState<RoleRecord[]>([]);
  const [editing, setEditing] = useState<RoleRecord | null | "new">(null);
  const [deleting, setDeleting] = useState<RoleRecord | null>(null);
  const [error, setError] = useState("");
  const load = () => { api<RoleRecord[]>("/api/roles").then(setRoles).catch((err) => setError(errorText(err))); };
  useEffect(load, []);
  return (
    <div>
      <PageHeader icon="admin_panel_settings" title="User Roles" subtitle="Each role grants none / read / write on Repositories, Insights and Settings." right={canWrite && <button type="button" className="btn btn-primary" onClick={() => setEditing("new")}><span className="material-symbols-outlined icon-sm">add</span>Add role</button>} />
      <div className="p-4">
        <ErrorNote message={error} />
        <div className="panel">
          <table className="w-full">
            <thead className="table-head"><tr><th>Role</th>{AREAS.map((a) => <th key={a.key}>{a.label}</th>)}<th className="text-right">Users</th><th /></tr></thead>
            <tbody>
              {roles.map((role) => (
                <tr key={role.role_id} className="table-row">
                  <td><div className="font-medium">{role.name} {role.is_system && <span className="chip ml-1 border-line text-faint">predefined</span>}</div><div className="text-body-sm text-faint">{role.description}</div></td>
                  {AREAS.map((a) => <td key={a.key}><span className={`chip ${LEVEL_CLS[role[a.key]]}`}>{role[a.key]}</span></td>)}
                  <td className="text-right font-mono tnum">{role.user_count}</td>
                  <td className="text-right">
                    {canWrite && !role.locked && <button type="button" className="icon-btn" title="Edit" onClick={() => setEditing(role)}><span className="material-symbols-outlined icon-sm">edit</span></button>}
                    {canWrite && !role.is_system && <button type="button" className="icon-btn hover:!text-critical" title="Delete" onClick={() => setDeleting(role)}><span className="material-symbols-outlined icon-sm">delete</span></button>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
      {editing && <RoleDialog role={editing === "new" ? null : editing} onClose={() => setEditing(null)} onSaved={() => { setEditing(null); load(); }} />}
      {deleting && <ConfirmDialog title="Delete role" message={`Delete the role ${deleting.name}?`} onClose={() => setDeleting(null)} onConfirm={async () => { try { await del(`/api/roles/${deleting.role_id}`); } catch (err) { setError(errorText(err)); } setDeleting(null); load(); }} />}
    </div>
  );
}
