import { useState, type FormEvent } from "react";
import { Navigate, useNavigate } from "react-router";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { ErrorMessage } from "../components/ui";

export default function Login() {
  const { user, setUser } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  if (user) return <Navigate to="/servers" replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setUser(await api.login(email, password));    // the gateway sets the session cookie
      navigate("/servers");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "login failed");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="container narrow">
      <article>
        <h2>Team MCP Gateway</h2>
        <form onSubmit={submit}>
          <label>Email<input type="email" autoComplete="username" required value={email}
                             onChange={(e) => setEmail(e.target.value)} /></label>
          <label>Password<input type="password" autoComplete="current-password" required value={password}
                                onChange={(e) => setPassword(e.target.value)} /></label>
          <ErrorMessage error={error} />
          <button type="submit" aria-busy={busy} disabled={busy}>Log in</button>
        </form>
        <small className="muted">New team member? Open the invite link your admin sent you.</small>
      </article>
    </main>
  );
}
