import { api, unwrap } from "./client";

export interface ApiKeyDTO {
  id: number;
  env: "testnet" | "mainnet";
  label: string;
  api_key_masked: string;
  created_at: string;
}

export const keysApi = {
  list: () => unwrap<ApiKeyDTO[]>(api.get("/keys")),
  save: (payload: {
    api_key: string;
    api_secret: string;
    label?: string;
    env?: "testnet" | "mainnet";
  }) => unwrap<any>(api.post("/keys", payload)),
  remove: (env?: "testnet" | "mainnet") =>
    unwrap<any>(
      api.delete("/keys", { params: env ? { env } : undefined }),
    ),
};
