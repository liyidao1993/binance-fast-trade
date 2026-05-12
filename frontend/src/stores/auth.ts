import { create } from "zustand";
import { authApi, AuthStatus } from "@/api/auth";
import { ws } from "@/api/ws";

interface AuthState {
  initialized: boolean;
  authenticated: boolean;
  env: "testnet" | "mainnet";
  loading: boolean;
  error: string | null;

  refreshStatus: () => Promise<void>;
  login: (pin: string) => Promise<void>;
  logout: () => Promise<void>;
  init: (pin: string) => Promise<void>;
}

export const useAuthStore = create<AuthState>((set) => ({
  initialized: false,
  authenticated: false,
  env: "testnet",
  loading: false,
  error: null,

  async refreshStatus() {
    try {
      const st: AuthStatus = await authApi.status();
      set({
        initialized: st.initialized,
        authenticated: st.authenticated,
        env: st.env,
      });
    } catch (e) {
      set({ authenticated: false, initialized: false });
    }
  },

  async login(pin: string) {
    set({ loading: true, error: null });
    try {
      await authApi.login(pin);
      set({ authenticated: true, loading: false });
    } catch (e: any) {
      set({ loading: false, error: e?.message || "登录失败" });
      throw e;
    }
  },

  async logout() {
    try {
      await authApi.logout();
    } catch {}
    ws.disconnect();
    set({ authenticated: false });
  },

  async init(pin: string) {
    set({ loading: true, error: null });
    try {
      await authApi.init(pin);
      set({ initialized: true, authenticated: true, loading: false });
    } catch (e: any) {
      set({ loading: false, error: e?.message || "初始化失败" });
      throw e;
    }
  },
}));
