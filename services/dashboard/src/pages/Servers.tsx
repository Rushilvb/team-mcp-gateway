// Upstream MCP servers: status and tools for everyone; add/edit/enable/disable/
// test/remove for admins. Every change returns after the gateway has already
// refreshed its tool list, so what you see is what MCP clients see.
import { useEffect, useState, type FormEvent } from "react";
import { api, formatTime, type AuthType, type Server, type ServerInput } from "../api";
import { useAuth } from "../auth";
import { Badge, ErrorMessage } from "../components/ui";

function ServerForm({ server, onDone, onCancel }: {
  server: Server | null;                 // null = add a new server
  onDone: () => void;
  onCancel: () => void;
}) {
  const [slug, setSlug] = useState(server?.slug ?? "");
  const [name, setName] = useState(server?.name ?? "");
  const [url, setUrl] = useState(server?.url ?? "");
  const [authType, setAuthType] = useState<AuthType>(server?.auth_type ?? "none");
  const [headerName, setHeaderName] = useState(server?.header_name ?? "X-API-Key");
  const [credential, setCredential] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    const input: ServerInput = {
      name, url, auth_type: authType,
      header_name: authType === "header" ? headerName : null,
    };
    // Credentials are write-only: blank on edit means "keep the current one".
    if (credential || !server) input.credential = credential || null;
    try {
      if (server) await api.updateServer(server.slug, input);
      else await api.createServer({ ...input, slug });
      onDone();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <article>
      <h3>{server ? `Edit ${server.name}` : "Add an MCP server"}</h3>
      <form onSubmit={submit}>
        <div className="grid">
          <label>Slug (tool prefix)
            <input value={slug} onChange={(e) => setSlug(e.target.value)} required disabled={!!server}
                   pattern="[a-z0-9-]+" placeholder="crm" />
            <small>Tools appear as <code>{slug || "slug"}__toolname</code>. Can't be changed later.</small>
          </label>
          <label>Name<input value={name} onChange={(e) => setName(e.target.value)} required /></label>
        </div>
        <label>URL (Streamable HTTP endpoint)
          <input value={url} onChange={(e) => setUrl(e.target.value)} required placeholder="https://mcp.example.com/mcp" />
        </label>
        <div className="grid">
          <label>Authentication
            <select value={authType} onChange={(e) => setAuthType(e.target.value as AuthType)}>
              <option value="none">None</option>
              <option value="bearer">Bearer token (Authorization header)</option>
              <option value="header">Custom header</option>
            </select>
          </label>
          {authType === "header" && (
            <label>Header name<input value={headerName} onChange={(e) => setHeaderName(e.target.value)} required /></label>
          )}
          {authType !== "none" && (
            <label>{authType === "bearer" ? "Token" : "Header value"}
              <input type="password" autoComplete="off" value={credential} onChange={(e) => setCredential(e.target.value)}
                     required={!server || !server.credential_set}
                     placeholder={server?.credential_set ? "set (leave blank to keep)" : ""} />
            </label>
          )}
        </div>
        <ErrorMessage error={error} />
        <div className="actions">
          <button type="submit" aria-busy={busy} disabled={busy}>{server ? "Save" : "Add server"}</button>
          <button type="button" className="secondary" onClick={onCancel}>Cancel</button>
        </div>
      </form>
    </article>
  );
}

function ServerCard({ server, isAdmin, onChange, onEdit }: {
  server: Server; isAdmin: boolean; onChange: () => void; onEdit: () => void;
}) {
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function run(action: () => Promise<string | void>) {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await action();
      if (result) setMessage(result);
      onChange();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <article>
      <div className="server-header">
        <h3>{server.name} <small className="muted"><code>{server.slug}</code></small></h3>
        <Badge value={server.status} />
      </div>
      <p>
        <small><code>{server.url}</code><br />
          Auth: {server.auth_type === "none" ? "none" : server.auth_type === "bearer" ? "bearer token" : `header ${server.header_name}`}
          {server.auth_type !== "none" && ` (credential ${server.credential_set ? "set" : "missing"})`}
          {" · "}checked {formatTime(server.last_checked_at)}</small>
      </p>
      {server.last_error && <p className="error"><small>Last error: {server.last_error}</small></p>}
      <details>
        <summary>{server.tools.length} tool{server.tools.length === 1 ? "" : "s"}</summary>
        <ul className="tools">
          {server.tools.map((t) => <li key={t.name}><code>{t.name}</code> {t.description}</li>)}
        </ul>
      </details>
      {isAdmin && (
        <div className="actions">
          <button className="secondary" disabled={busy}
                  onClick={() => run(() => api.updateServer(server.slug, { enabled: !server.enabled }).then(() => {}))}>
            {server.enabled ? "Disable" : "Enable"}
          </button>
          <button className="secondary" disabled={busy}
                  onClick={() => run(async () => {
                    const r = await api.testServer(server.slug);
                    return r.status === "connected" ? `Connected: ${r.tool_count} tools` : `${r.status}: ${r.last_error ?? ""}`;
                  })}>Test</button>
          <button className="secondary" disabled={busy} onClick={onEdit}>Edit</button>
          <button className="secondary outline" disabled={busy}
                  onClick={() => confirm(`Remove ${server.name}? Its tools disappear for everyone.`)
                    && run(() => api.deleteServer(server.slug))}>Remove</button>
        </div>
      )}
      {message && <p><small>{message}</small></p>}
      <ErrorMessage error={error} />
    </article>
  );
}

export default function Servers() {
  const { user } = useAuth();
  const isAdmin = user?.role === "admin";
  const [servers, setServers] = useState<Server[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Server | "new" | null>(null);

  function load() {
    api.servers().then(setServers).catch((err) => setError(err.message));
  }
  useEffect(load, []);

  return (
    <>
      <div className="server-header">
        <h2>MCP servers</h2>
        <div className="actions">
          <button className="secondary outline" onClick={load}>Refresh</button>
          {isAdmin && !editing && <button onClick={() => setEditing("new")}>Add server</button>}
        </div>
      </div>
      <p className="muted">
        Every tool of every connected server is available through one endpoint, named <code>server__tool</code>.
        {!isAdmin && " Only admins can change servers."}
      </p>
      <ErrorMessage error={error} />
      {editing && (
        <ServerForm server={editing === "new" ? null : editing}
                    onDone={() => { setEditing(null); load(); }} onCancel={() => setEditing(null)} />
      )}
      {servers === null && !error && <p aria-busy="true">Loading…</p>}
      {servers?.map((s) => (
        <ServerCard key={s.slug} server={s} isAdmin={isAdmin} onChange={load} onEdit={() => setEditing(s)} />
      ))}
    </>
  );
}
