import { api, unwrap } from "./client";

export interface GuardRules {
  per_order_max_usdt: number;
  global_max_position_usdt: number;
  global_max_leverage: number;
  allowed_symbols: string[];
  forbidden_symbols: string[];
  ai_review_default: string;
  require_pin_for: string[];
}

export const guardApi = {
  get: () => unwrap<GuardRules>(api.get("/guard/rules")),
  put: (rules: GuardRules) => unwrap<GuardRules>(api.put("/guard/rules", rules)),
  reset: () => unwrap<GuardRules>(api.post("/guard/reset")),
};
