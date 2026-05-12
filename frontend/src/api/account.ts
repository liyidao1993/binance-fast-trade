import { api, unwrap } from "./client";

export interface AccountSummary {
  balance: {
    usdt_total: string;
    usdt_available: string;
    total_wallet: string | null;
    total_unrealized_pnl: string | null;
    total_margin_balance: string | null;
  };
  open_positions: any[];
  open_orders: any[];
  running_strategies: {
    run_id: number;
    strategy_id: number;
    strategy_name: string;
    strategy_type: string;
    started_at: string | null;
    last_heartbeat: string | null;
  }[];
}

export const accountApi = {
  summary: () => unwrap<AccountSummary>(api.get("/account/summary")),
};
