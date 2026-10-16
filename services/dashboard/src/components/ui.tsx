// Small shared UI pieces.
import { useState } from "react";

const STATUS_LABEL: Record<string, string> = {
  connected: "Connected", unreachable: "Unreachable", disabled: "Disabled", unknown: "Checking…",
  active: "Active", invited: "Invited",
  success: "success", error: "error", timeout: "timeout", denied: "denied",
};

export function Badge({ value }: { value: string }) {
  return <span className={`badge badge-${value}`}>{STATUS_LABEL[value] ?? value}</span>;
}

export function ErrorMessage({ error }: { error: string | null }) {
  return error ? <p className="error" role="alert">{error}</p> : null;
}

// navigator.clipboard only exists on HTTPS (or localhost); locally we run plain
// HTTP on app.localtest.me, so fall back to the older select-and-copy approach.
async function copyText(text: string): Promise<boolean> {
  if (navigator.clipboard) {
    try { await navigator.clipboard.writeText(text); return true; } catch { /* fall through */ }
  }
  const area = document.createElement("textarea");
  area.value = text;
  document.body.appendChild(area);
  area.select();
  const ok = document.execCommand("copy");
  area.remove();
  return ok;
}

export function CopyBox({ value, label }: { value: string; label?: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="copybox">
      {label && <small>{label}</small>}
      <div role="group">
        <input readOnly value={value} onFocus={(e) => e.currentTarget.select()} />
        <button type="button" className="secondary" onClick={async () => setCopied(await copyText(value))}>
          {copied ? "Copied" : "Copy"}
        </button>
      </div>
    </div>
  );
}

export function CodeBlock({ code }: { code: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div className="codeblock">
      <pre><code>{code}</code></pre>
      <button type="button" className="secondary outline" onClick={async () => setCopied(await copyText(code))}>
        {copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}
