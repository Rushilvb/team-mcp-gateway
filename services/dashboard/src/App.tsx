// Routes and the navigation bar. Admin-only links are hidden from members for
// convenience; the gateway enforces roles on every request regardless.
import type { ReactNode } from "react";
import { BrowserRouter, Navigate, NavLink, Route, Routes } from "react-router";
import { AuthProvider, useAuth } from "./auth";
import AcceptInvite from "./pages/AcceptInvite";
import ApiKeys from "./pages/ApiKeys";
import Audit from "./pages/Audit";
import Chat from "./pages/Chat";
import Login from "./pages/Login";
import Members from "./pages/Members";
import Servers from "./pages/Servers";

function Layout({ children }: { children: ReactNode }) {
  const { user, logout } = useAuth();
  return (
    <>
      <header className="container">
        <nav>
          <ul><li><strong>Team MCP Gateway</strong></li></ul>
          <ul>
            <li><NavLink to="/servers">Servers</NavLink></li>
            <li><NavLink to="/keys">API keys</NavLink></li>
            <li><NavLink to="/chat">Chat</NavLink></li>
            <li><NavLink to="/audit">Audit log</NavLink></li>
            {user?.role === "admin" && <li><NavLink to="/members">Members</NavLink></li>}
            <li><small>{user?.email} ({user?.role})</small></li>
            <li><button className="outline secondary" onClick={logout}>Log out</button></li>
          </ul>
        </nav>
      </header>
      <main className="container">{children}</main>
    </>
  );
}

function RequireLogin({ children, admin = false }: { children: ReactNode; admin?: boolean }) {
  const { user } = useAuth();
  if (!user) return <Navigate to="/login" replace />;
  if (admin && user.role !== "admin") return <Navigate to="/servers" replace />;
  return <Layout>{children}</Layout>;
}

function AppRoutes() {
  const { loading } = useAuth();
  if (loading) return <main className="container" aria-busy="true">Loading…</main>;
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/invite/:token" element={<AcceptInvite />} />
      <Route path="/servers" element={<RequireLogin><Servers /></RequireLogin>} />
      <Route path="/keys" element={<RequireLogin><ApiKeys /></RequireLogin>} />
      <Route path="/chat" element={<RequireLogin><Chat /></RequireLogin>} />
      <Route path="/audit" element={<RequireLogin><Audit /></RequireLogin>} />
      <Route path="/members" element={<RequireLogin admin><Members /></RequireLogin>} />
      <Route path="*" element={<Navigate to="/servers" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRoutes />
      </AuthProvider>
    </BrowserRouter>
  );
}
