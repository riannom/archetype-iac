import { useEffect, useState } from "react";
import { NavLink, Navigate, Outlet, useNavigate } from "react-router-dom";
import { applyTheme, getPreferredTheme, toggleTheme } from "../theme";
import { AuraLogo } from "./AuraLogo";

export function Layout() {
  const token = localStorage.getItem("token");
  const [theme, setTheme] = useState<"light" | "dark">("light");
  const navigate = useNavigate();

  useEffect(() => {
    const preferred = getPreferredTheme();
    applyTheme(preferred);
    setTheme(preferred);
  }, []);

  function handleToggleTheme() {
    setTheme(toggleTheme());
  }

  function handleLogout() {
    localStorage.removeItem("token");
    navigate("/auth/login");
  }

  if (!token) {
    return <Navigate to="/auth/login" replace />;
  }
  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="sidebar-header">
          <div className="brand-mark" aria-hidden="true">
            <AuraLogo className="brand-logo" />
          </div>
          <div>
            <div className="brand-name">Aura</div>
            <div className="brand-subtitle">an IaC Canvas</div>
          </div>
        </div>
        <div className="sidebar-section-title">Workspace</div>
        <nav className="nav">
          <NavLink to="/labs">
            <svg viewBox="0 0 24 24" className="nav-icon" aria-hidden="true">
              <rect x="4" y="4" width="16" height="16" rx="3" />
              <path d="M8 8h8M8 12h8M8 16h6" strokeWidth="1.4" />
            </svg>
            Labs
          </NavLink>
          <NavLink to="/studio">
            <svg viewBox="0 0 24 24" className="nav-icon" aria-hidden="true">
              <rect x="3" y="5" width="18" height="14" rx="2" />
              <path d="M7 9h10M7 13h6" strokeWidth="1.4" />
            </svg>
            Studio
          </NavLink>
          {!token && (
            <NavLink to="/auth/login">
              <svg viewBox="0 0 24 24" className="nav-icon" aria-hidden="true">
                <path d="M7 7h6a4 4 0 0 1 4 4v6" strokeWidth="1.6" />
                <rect x="3" y="11" width="10" height="10" rx="2" />
                <path d="M13 16h8M18 12l3 4-3 4" strokeWidth="1.6" />
              </svg>
              Login
            </NavLink>
          )}
        </nav>
        <div className="sidebar-section-title">Catalog</div>
        <nav className="nav">
          <NavLink to="/catalog">
            <svg viewBox="0 0 24 24" className="nav-icon" aria-hidden="true">
              <rect x="5" y="4" width="14" height="4" rx="1.5" />
              <rect x="5" y="10" width="14" height="4" rx="1.5" />
              <rect x="5" y="16" width="14" height="4" rx="1.5" />
            </svg>
            Devices & images
          </NavLink>
        </nav>
        <div className="sidebar-footer">
          <div className="sidebar-actions">
            <button className="theme-toggle" onClick={handleToggleTheme} type="button">
              {theme === "dark" ? "Light mode" : "Dark mode"}
            </button>
            {token && (
              <button className="theme-toggle" onClick={handleLogout} type="button">
                Log out
              </button>
            )}
          </div>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="topbar-title">
            <span className="topbar-icon">🧩</span>
            Aura
          </div>
          <div className="topbar-meta" />
        </header>
        <section className="content">
          <Outlet />
        </section>
      </main>
    </div>
  );
}
