import { api, unwrap } from "./client";

export interface AuthStatus {
  initialized: boolean;
  authenticated: boolean;
  env: "testnet" | "mainnet";
}

export const authApi = {
  status: () => unwrap<AuthStatus>(api.get("/auth/status")),
  login: (pin: string) =>
    unwrap<{ authenticated: boolean; api_key_ready: boolean }>(
      api.post("/auth/login", { pin }),
    ),
  logout: () => unwrap<{ authenticated: boolean }>(api.post("/auth/logout")),
  init: (pin: string) =>
    unwrap<{ initialized: boolean; message: string }>(
      api.post("/auth/init", { pin }),
    ),
};
