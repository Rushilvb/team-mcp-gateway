// Admin only: invite members, change roles, enable/disable, see and revoke their keys.
import { Fragment, useEffect, useState, type FormEvent } from "react";
import { api, formatTime, type ApiKey, type Role, type User } from "../api";
import { useAuth } from "../auth";
import { Badge, CopyBox, ErrorMessage } from "../components/ui";

function MemberKeys({ user }: { user: User }) {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const load = () => { api.userKeys(user.id).then(setKeys).catch((e) => setError(e.message)); };
  useEffect(load, [user.id]);

  if (error) return <ErrorMessage error={error} />;
  if (!keys) return <p aria-busy="true">Loading keys…</p>;
  if (keys.length === 0) return <p className="muted"><small>No API keys.</small></p>;
  return (
    <table>
      <thead><tr><th>Key</th><th>Name</th><th>Last used</th><th></th></tr></thead>
      <tbody>
        {keys.map((k) => (
          <tr key={k.id}>
            <td><code>{k.prefix}…</code></td>
            <td>{k.name}</td>
            <td>{formatTime(k.last_used_at)}</td>
            <td>{k.revoked_at ? <Badge value="disabled" /> : (
              <button className="secondary outline" onClick={() => api.revokeKey(k.id).then(load)}>Revoke</button>
            )}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}

export default function Members() {
  const { user: me } = useAuth();
  const [users, setUsers] = useState<User[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [email, setEmail] = useState("");
  const [role, setRole] = useState<Role>("member");
  const [inviteUrl, setInviteUrl] = useState<{ email: string; url: string } | null>(null);
  const [openKeys, setOpenKeys] = useState<number | null>(null);

  const load = () => { api.users().then(setUsers).catch((e) => setError(e.message)); };
  useEffect(load, []);

  async function act(action: () => Promise<unknown>) {
    setError(null);
    try { await action(); load(); } catch (err) { setError((err as Error).message); }
  }

  async function invite(e: FormEvent) {
    e.preventDefault();
    await act(async () => {
      const r = await api.inviteUser(email, role);
      setInviteUrl({ email: r.user.email, url: r.invite_url });
      setEmail("");
    });
  }

  return (
    <>
      <h2>Members</h2>
      <article>
        <h3>Invite a member</h3>
        <form onSubmit={invite}>
          <fieldset role="group">
            <input type="email" placeholder="name@company.com" required value={email}
                   onChange={(e) => setEmail(e.target.value)} />
            <select value={role} onChange={(e) => setRole(e.target.value as Role)} aria-label="Role">
              <option value="member">member</option>
              <option value="admin">admin</option>
            </select>
            <button type="submit">Invite</button>
          </fieldset>
        </form>
        {inviteUrl && (
          <div className="key-once">
            <CopyBox value={inviteUrl.url}
                     label={`One-time link for ${inviteUrl.email} (valid 7 days, shown only now; send it to them):`} />
          </div>
        )}
      </article>
      <ErrorMessage error={error} />
      {users === null && !error && <p aria-busy="true">Loading…</p>}
      {users && (
        <table>
          <thead><tr><th>Email</th><th>Role</th><th>Status</th><th>Active keys</th><th>Actions</th></tr></thead>
          <tbody>
            {users.map((u) => (
              <Fragment key={u.id}>
                <tr>
                  <td>{u.email}{u.id === me?.id && <small className="muted"> (you)</small>}</td>
                  <td>
                    <select value={u.role} aria-label={`Role of ${u.email}`}
                            onChange={(e) => act(() => api.updateUser(u.id, { role: e.target.value as Role }))}>
                      <option value="member">member</option>
                      <option value="admin">admin</option>
                    </select>
                  </td>
                  <td><Badge value={u.status} /></td>
                  <td>{u.active_keys}</td>
                  <td className="actions">
                    {u.status === "invited" ? (
                      <button className="secondary" onClick={() => act(async () => {
                        const r = await api.reissueInvite(u.id);
                        setInviteUrl({ email: r.user.email, url: r.invite_url });
                      })}>New invite link</button>
                    ) : (
                      <button className="secondary" onClick={() => act(() =>
                        api.updateUser(u.id, { status: u.status === "active" ? "disabled" : "active" }))}>
                        {u.status === "active" ? "Disable" : "Enable"}
                      </button>
                    )}
                    <button className="secondary outline" onClick={() => setOpenKeys(openKeys === u.id ? null : u.id)}>
                      {openKeys === u.id ? "Hide keys" : "Keys"}
                    </button>
                  </td>
                </tr>
                {openKeys === u.id && (
                  <tr><td colSpan={5}><MemberKeys user={u} /></td></tr>
                )}
              </Fragment>
            ))}
          </tbody>
        </table>
      )}
    </>
  );
}
