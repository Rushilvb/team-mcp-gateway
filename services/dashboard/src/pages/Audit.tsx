// Audit log: every tool call through the gateway. Members only ever get their
// own rows: the gateway forces that filter, whatever this page asks for.
import { useEffect, useState } from "react";
import { api, formatTime, type AuditPage, type Server, type User } from "../api";
import { useAuth } from "../auth";
import { Badge, ErrorMessage } from "../components/ui";

const PAGE_SIZE = 25;

export default function Audit() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [filters, setFilters] = useState({ user_id: "", server: "", outcome: "", channel: "", since: "", until: "" });
  const [offset, setOffset] = useState(0);
  const [page, setPage] = useState<AuditPage | null>(null);
  const [users, setUsers] = useState<User[]>([]);
  const [servers, setServers] = useState<Server[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.servers().then(setServers).catch(() => undefined);
    if (isAdmin) api.users().then(setUsers).catch(() => undefined);
  }, [isAdmin]);

  function load() {
    setError(null);
    api.audit({
      ...filters,
      // <input type="date"> gives YYYY-MM-DD in local time; send ISO instants.
      since: filters.since ? new Date(`${filters.since}T00:00`).toISOString() : undefined,
      until: filters.until ? new Date(`${filters.until}T23:59:59`).toISOString() : undefined,
      limit: PAGE_SIZE,
      offset,
    }).then(setPage).catch((e) => setError(e.message));
  }
  useEffect(load, [filters, offset]);

  const set = (key: keyof typeof filters) => (e: { target: { value: string } }) => {
    setOffset(0);
    setFilters({ ...filters, [key]: e.target.value });
  };

  return (
    <>
      <h2>Audit log</h2>
      <p className="muted">{isAdmin ? "Every tool call by every member." : "Your tool calls."}</p>
      <div className="filters">
        {isAdmin && (
          <label>User<select value={filters.user_id} onChange={set("user_id")}>
            <option value="">all</option>
            {users.map((u) => <option key={u.id} value={u.id}>{u.email}</option>)}
          </select></label>
        )}
        <label>Server<select value={filters.server} onChange={set("server")}>
          <option value="">all</option>
          {servers.map((s) => <option key={s.slug} value={s.slug}>{s.slug}</option>)}
        </select></label>
        <label>Outcome<select value={filters.outcome} onChange={set("outcome")}>
          <option value="">all</option>
          {["success", "error", "timeout", "denied"].map((o) => <option key={o}>{o}</option>)}
        </select></label>
        <label>Channel<select value={filters.channel} onChange={set("channel")}>
          <option value="">all</option><option>api</option><option>chat</option>
        </select></label>
        <label>From<input type="date" value={filters.since} onChange={set("since")} /></label>
        <label>To<input type="date" value={filters.until} onChange={set("until")} /></label>
      </div>
      <ErrorMessage error={error} />
      {page && (
        <>
          <table>
            <thead><tr><th>Time</th><th>User</th><th>Channel</th><th>Tool</th><th>Outcome</th><th>ms</th><th>Error</th></tr></thead>
            <tbody>
              {page.items.map((a) => (
                <tr key={a.id}>
                  <td>{formatTime(a.started_at)}</td>
                  <td>{a.user_email ?? "-"}</td>
                  <td>{a.channel}</td>
                  <td><code>{a.tool}</code></td>
                  <td><Badge value={a.outcome} /></td>
                  <td>{a.duration_ms}</td>
                  <td><small>{a.error_message}</small></td>
                </tr>
              ))}
              {page.items.length === 0 && <tr><td colSpan={7} className="muted">No matching calls.</td></tr>}
            </tbody>
          </table>
          <div className="pager">
            <button className="secondary" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}>Newer</button>
            <span>{page.total === 0 ? 0 : offset + 1}–{Math.min(offset + PAGE_SIZE, page.total)} of {page.total}</span>
            <button className="secondary" disabled={offset + PAGE_SIZE >= page.total} onClick={() => setOffset(offset + PAGE_SIZE)}>Older</button>
            <button className="secondary outline" onClick={load}>Refresh</button>
          </div>
        </>
      )}
    </>
  );
}
