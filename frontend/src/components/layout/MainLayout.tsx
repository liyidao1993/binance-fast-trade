import { NavLink, Outlet } from "react-router-dom";
import { useAuthStore } from "@/stores/auth";

const items = [
  { to: "/trade", label: "交易" },
  { to: "/strategies", label: "策略" },
  { to: "/settings", label: "设置" },
];

export default function MainLayout() {
  const env = useAuthStore((s) => s.env);
  const logout = useAuthStore((s) => s.logout);

  return (
    <div className="min-h-screen flex flex-col">
      {/* Header */}
      <header className="border-b border-border bg-bg-soft/80 backdrop-blur">
        <div className="flex items-center gap-4 px-4 h-12">
          <div className="font-semibold">BFT</div>
          <span
            className={
              "tag " +
              (env === "mainnet"
                ? "text-accent-down border-accent-down/50"
                : "text-accent-info border-accent-info/40")
            }
          >
            {env}
          </span>

          {/* 桌面端导航 */}
          <nav className="hidden sm:flex items-center gap-1 ml-6">
            {items.map((it) => (
              <NavLink
                key={it.to}
                to={it.to}
                className={({ isActive }) =>
                  "px-3 py-1.5 rounded text-sm " +
                  (isActive
                    ? "bg-bg-raised text-white"
                    : "text-text-muted hover:text-text hover:bg-bg")
                }
              >
                {it.label}
              </NavLink>
            ))}
          </nav>

          <div className="ml-auto">
            <button className="btn" onClick={logout}>
              登出
            </button>
          </div>
        </div>
      </header>

      <main className="flex-1 p-3 sm:p-4 overflow-auto">
        <Outlet />
      </main>

      {/* 移动端底部导航 */}
      <nav className="sm:hidden border-t border-border bg-bg-soft grid grid-cols-3">
        {items.map((it) => (
          <NavLink
            key={it.to}
            to={it.to}
            className={({ isActive }) =>
              "text-center py-3 text-xs " +
              (isActive ? "text-white bg-bg-raised" : "text-text-muted")
            }
          >
            {it.label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
