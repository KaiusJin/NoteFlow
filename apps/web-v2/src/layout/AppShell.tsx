import { NavLink, Outlet } from "react-router-dom";
import { useAuth } from "../auth/AuthProvider";

const navigation = [
  { to: "/", label: "Home", icon: "⌂", end: true },
  { to: "/documents", label: "Documents", icon: "▤" },
  { to: "/search", label: "Search", icon: "⌕" },
  { to: "/agent", label: "AI Agent", icon: "✦" },
  { to: "/study", label: "Study", icon: "◇" },
  { to: "/settings", label: "Settings", icon: "⚙" }
];

export function AppShell() {
  const auth = useAuth();
  const initials = auth.user?.email?.slice(0, 2).toUpperCase() ?? "NF";

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <NavLink className="brand" to="/" aria-label="NoteFlow home">
          <span className="brand-mark">N</span><span>NoteFlow</span>
        </NavLink>
        <nav className="navigation" aria-label="Primary navigation">
          {navigation.map((item) => (
            <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => isActive ? "nav-item active" : "nav-item"}>
              <span className="nav-icon" aria-hidden="true">{item.icon}</span>
              <span>{item.label}</span>
            </NavLink>
          ))}
        </nav>
        <div className="sidebar-account">
          <span className="avatar">{initials}</span>
          <span className="account-copy"><strong>{auth.user?.email?.split("@")[0]}</strong><small>Personal workspace</small></span>
          <button type="button" className="icon-button" aria-label="Sign out" title="Sign out" onClick={() => void auth.signOut()}>↗</button>
        </div>
      </aside>
      <main className="main-content">
        <Outlet />
      </main>
      <nav className="mobile-navigation" aria-label="Mobile navigation">
        {navigation.slice(0, 5).map((item) => (
          <NavLink key={item.to} to={item.to} end={item.end} className={({ isActive }) => isActive ? "mobile-nav-item active" : "mobile-nav-item"}>
            <span aria-hidden="true">{item.icon}</span><small>{item.label}</small>
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
