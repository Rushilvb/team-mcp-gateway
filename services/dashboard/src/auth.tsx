// Who is logged in, shared with every page through React context.
// On load we ask the gateway (/api/auth/me): the session cookie is httpOnly,
// so JavaScript can't read it, and the server is the only source of truth.
import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { api, type Me } from "./api";

interface AuthState {
  user: Me | null;
  loading: boolean;
  setUser: (user: Me | null) => void;
  logout: () => Promise<void>;
}

const AuthContext = createContext<AuthState | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<Me | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    api.me().then(setUser).catch(() => setUser(null)).finally(() => setLoading(false));
  }, []);

  async function logout() {
    await api.logout().catch(() => undefined);
    setUser(null);
  }

  return <AuthContext.Provider value={{ user, loading, setUser, logout }}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthState {
  const ctx = useContext(AuthContext);
  if (!ctx) throw new Error("useAuth must be used inside <AuthProvider>");
  return ctx;
}
