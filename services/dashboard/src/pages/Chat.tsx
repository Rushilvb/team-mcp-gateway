// Chat with the agent. Every tool it uses runs through the gateway as YOU
// (audited with channel "chat"); the chips under each answer show those calls.
// The conversation lives only in this page: the server stores none of it.
import { useEffect, useRef, useState, type FormEvent } from "react";
import { chatApi, type ChatMessage, type ToolCallChip } from "../api";
import { ErrorMessage } from "../components/ui";

interface Entry extends ChatMessage {
  toolCalls?: ToolCallChip[];
}

const SUGGESTIONS = [
  "What orders came in today?",
  "Create a product called wireless earbuds priced 1999",
  "List today's orders and convert the total to USD",
  "Summarize today's orders and save it as a note",
];

function ToolChip({ call }: { call: ToolCallChip }) {
  return (
    <details className={`chip ${call.ok ? "chip-ok" : "chip-error"}`}>
      <summary><code>{call.name}</code> {call.ok ? "✓" : "✗"}</summary>
      <small>Arguments</small>
      <pre><code>{JSON.stringify(call.arguments, null, 2)}</code></pre>
      <small>{call.ok ? "Result" : "Error"}</small>
      <pre><code>{call.ok ? call.result : call.error}</code></pre>
    </details>
  );
}

export default function Chat() {
  const [entries, setEntries] = useState<Entry[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  useEffect(() => { bottom.current?.scrollIntoView({ behavior: "smooth" }); }, [entries, busy]);

  async function send(text: string) {
    if (!text.trim() || busy) return;
    const conversation: Entry[] = [...entries, { role: "user", text: text.trim() }];
    setEntries(conversation);
    setInput("");
    setBusy(true);
    setError(null);
    try {
      // Only the text turns are sent back; tool details stay on this page.
      const reply = await chatApi.send(conversation.map(({ role, text }) => ({ role, text })));
      setEntries([...conversation, { role: "assistant", text: reply.answer, toolCalls: reply.tool_calls }]);
    } catch (err) {
      setError((err as Error).message);
      setEntries(entries);          // put the conversation back as it was
      setInput(text);
    } finally {
      setBusy(false);
    }
  }

  function submit(e: FormEvent) {
    e.preventDefault();
    void send(input);
  }

  return (
    <>
      <div className="server-header">
        <h2>Chat</h2>
        {entries.length > 0 && (
          <button className="secondary outline" disabled={busy} onClick={() => { setEntries([]); setError(null); }}>
            New conversation
          </button>
        )}
      </div>
      <p className="muted">
        The assistant uses the team's tools on your behalf. Every call is in the audit log under your name (channel <code>chat</code>).
      </p>

      {entries.length === 0 && (
        <div className="actions suggestions">
          {SUGGESTIONS.map((s) => (
            <button key={s} className="secondary outline" onClick={() => send(s)} disabled={busy}>{s}</button>
          ))}
        </div>
      )}

      <div className="chat-log">
        {entries.map((entry, i) => (
          <article key={i} className={`msg msg-${entry.role}`}>
            <small className="muted">{entry.role === "user" ? "You" : "Assistant"}</small>
            <p className="msg-text">{entry.text}</p>
            {entry.toolCalls && entry.toolCalls.length > 0 && (
              <div className="chips">{entry.toolCalls.map((c, j) => <ToolChip key={j} call={c} />)}</div>
            )}
          </article>
        ))}
        {busy && <article className="msg msg-assistant" aria-busy="true">Working: calling tools through the gateway…</article>}
        <div ref={bottom} />
      </div>

      <ErrorMessage error={error} />
      <form onSubmit={submit}>
        <fieldset role="group">
          <input placeholder="Ask about orders, products, notes, currency…" value={input} maxLength={8000}
                 onChange={(e) => setInput(e.target.value)} disabled={busy} autoFocus />
          <button type="submit" disabled={busy || !input.trim()}>Send</button>
        </fieldset>
      </form>
    </>
  );
}
