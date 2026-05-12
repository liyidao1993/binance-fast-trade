import { useEffect } from "react";
import { Routes, Route, Navigate } from "react-router-dom";
import LoginPage from "@/pages/Login";
import TradePage from "@/pages/Trade";
import StrategiesPage from "@/pages/Strategies";
import SettingsPage from "@/pages/Settings";
import MainLayout from "@/components/layout/MainLayout";
import { useAuthStore } from "@/stores/auth";
import { useWs } from "@/api/ws";

function RequireAuth({ children }: { children: React.ReactNode }) {
  const authed = useAuthStore((s) => s.authenticated);
  if (!authed) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

export default function App() {
  const refreshStatus = useAuthStore((s) => s.refreshStatus);
  const authed = useAuthStore((s) => s.authenticated);

  // 启动：拉一次 /auth/status
  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  // WS：登录后自动连接，登出后断开
  useWs(authed);

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route
        path="/"
        element={
          <RequireAuth>
            <MainLayout />
          </RequireAuth>
        }
      >
        <Route index element={<Navigate to="/trade" replace />} />
        <Route path="trade" element={<TradePage />} />
        <Route path="strategies" element={<StrategiesPage />} />
        <Route path="settings" element={<SettingsPage />} />
      </Route>
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}
