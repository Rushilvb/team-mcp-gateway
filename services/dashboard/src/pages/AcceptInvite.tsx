import { useEffect, useState, type FormEvent } from "react";
import { useNavigate, useParams } from "react-router";
import { api, ApiError } from "../api";
import { useAuth } from "../auth";
import { ErrorMessage } from "../components/ui";

export default function AcceptInvite() {
  const { token = "" } = useParams();
  const { setUser } = useAuth();
  const navigate = useNavigate();
  const [email, setEmail] = useState<string | null>(null);
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api.inviteInfo(token).then((r) => setEmail(r.email)).catch((err) => setError(err.message));
  }, [token]);

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (password !== confirm) return setError("passwords don't match");
    try {
      setUser(await api.acceptInvite(token, password));   // also logs you in
      navigate("/keys");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "could not accept the invite");
    }
  }

  return (
    <main className="container narrow">
      <article>
        <h2>Join the team</h2>
        {email === null && !error && <p aria-busy="true">Checking your invite…</p>}
        {email && (
          <form onSubmit={submit}>
            <p>Set a password for <strong>{email}</strong>.</p>
            <label>Password (at least 10 characters)
              <input type="password" autoComplete="new-password" minLength={10} required value={password}
                     onChange={(e) => setPassword(e.target.value)} />
            </label>
            <label>Repeat password
              <input type="password" autoComplete="new-password" required value={confirm}
                     onChange={(e) => setConfirm(e.target.value)} />
            </label>
            <ErrorMessage error={error} />
            <button type="submit">Create my account</button>
          </form>
        )}
        {!email && <ErrorMessage error={error} />}
      </article>
    </main>
  );
}
