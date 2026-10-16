// Your own API keys (create: shown once; revoke) and how to connect an MCP client.
import { useEffect, useState, type FormEvent } from "react";
import { api, formatTime, type ApiKey } from "../api";
import { Badge, CodeBlock, CopyBox, ErrorMessage } from "../components/ui";

function ConnectSnippets({ apiKey }: { apiKey: string | null }) {
  const url = `${window.location.origin}/mcp`;
  const key = apiKey ?? "urk_YOUR_KEY";
  return (
    <article>
      <h3>Connect your MCP client</h3>
      <p>All you need is the gateway URL and one of your API keys. You'll see every tool as <code>server__tool</code>.</p>
      <CopyBox value={url} label="Gateway URL (Streamable HTTP)" />
      <h4>Claude Code</h4>
      <CodeBlock code={`claude mcp add --transport http team-gateway ${url} --header "Authorization: Bearer ${key}"`} />
      <h4>Cursor (~/.cursor/mcp.json)</h4>
      <CodeBlock code={JSON.stringify({ mcpServers: { "team-gateway": { url, headers: { Authorization: `Bearer ${key}` } } } }, null, 2)} />
      <h4>MCP Inspector</h4>
      <CodeBlock code={`npx @modelcontextprotocol/inspector@2.8.0
# Transport: Streamable HTTP   URL: ${url}
# Custom header: Authorization: Bearer ${key}`} />
      {!apiKey && <small className="muted">Create a key above and the snippets will include it.</small>}
    </article>
  );
}

export default function ApiKeys() {
  const [keys, setKeys] = useState<ApiKey[] | null>(null);
  const [name, setName] = useState("");
  const [newKey, setNewKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = () => { api.myKeys().then(setKeys).catch((e) => setError(e.message)); };
  useEffect(load, []);

  async function create(e: FormEvent) {
    e.preventDefault();
    setError(null);
    try {
      const r = await api.createKey(name);
      setNewKey(r.key);          // kept only in this page's memory; the server stores just a hash
      setName("");
      load();
    } catch (err) {
      setError((err as Error).message);
    }
  }

  async function revoke(id: number) {
    if (!confirm("Revoke this key? Clients using it stop working immediately.")) return;
    await api.revokeKey(id).catch((err) => setError(err.message));
    load();
  }

  return (
    <>
      <h2>My API keys</h2>
      <article>
        <form onSubmit={create}>
          <fieldset role="group">
            <input placeholder="Key name, e.g. laptop" value={name} maxLength={100}
                   onChange={(e) => setName(e.target.value)} />
            <button type="submit">Create key</button>
          </fieldset>
        </form>
        {newKey && (
          <div className="key-once">
            <CopyBox value={newKey} label="Your new key. Copy it now: it will never be shown again." />
          </div>
        )}
      </article>
      <ErrorMessage error={error} />
      {keys && keys.length > 0 && (
        <table>
          <thead><tr><th>Key</th><th>Name</th><th>Created</th><th>Last used</th><th></th></tr></thead>
          <tbody>
            {keys.map((k) => (
              <tr key={k.id}>
                <td><code>{k.prefix}…</code></td>
                <td>{k.name}</td>
                <td>{formatTime(k.created_at)}</td>
                <td>{formatTime(k.last_used_at)}</td>
                <td>{k.revoked_at ? <Badge value="disabled" /> :
                  <button className="secondary outline" onClick={() => revoke(k.id)}>Revoke</button>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {keys?.length === 0 && <p className="muted">No keys yet.</p>}
      <ConnectSnippets apiKey={newKey} />
    </>
  );
}
