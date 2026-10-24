import { NavLink, Outlet } from "react-router-dom";
import { SplitPane } from "../../components/ui";

const ITEMS = [
  { to: "/settings/llm", icon: "smart_toy", label: "LLM" },
  { to: "/settings/users", icon: "group", label: "Users" },
  { to: "/settings/roles", icon: "admin_panel_settings", label: "User Roles" },
  { to: "/settings/database", icon: "database", label: "Database" },
  { to: "/settings/audit", icon: "history", label: "Audit Log" },
];

export function SettingsLayout() {
  return (
    <SplitPane
      direction="horizontal"
      primary="first"
      initialSize={220}
      minSize={150}
      maxSize={420}
      first={
        <aside className="h-full w-full bg-surface">
          <div className="panel-head"><span>Administration</span></div>
          <nav className="p-2 space-y-0.5">
            {ITEMS.map((item) => (
              <NavLink key={item.to} to={item.to} className={({ isActive }) => `flex items-center gap-2 px-2 h-8 font-mono text-code border-l-2 ${isActive ? "border-primary bg-elevated text-primary" : "border-transparent text-muted hover:text-ink"}`}>
                <span className="material-symbols-outlined icon-sm">{item.icon}</span>
                {item.label}
              </NavLink>
            ))}
          </nav>
        </aside>
      }
      second={<div className="h-full min-w-0 overflow-auto"><Outlet /></div>}
    />
  );
}
