import { api, unwrap } from "./client";

export interface StrategyType {
  type_key: string;
  display_name: string;
  params_schema: Record<string, any>;
  stoploss_schema: Record<string, any>;
}

export interface StrategyDTO {
  id: number;
  name: string;
  type: string;
  params: Record<string, any>;
  stoploss: Record<string, any>;
  symbols: string[];
  is_template: boolean;
  running_count: number;
  created_at: string;
  updated_at: string;
}

export interface StrategyRunDTO {
  id: number;
  strategy_id: number;
  status: string;
  raw_status: string;
  task_name: string | null;
  started_at: string | null;
  stopped_at: string | null;
  last_heartbeat: string | null;
  last_error: string | null;
  stats: Record<string, any>;
}

export interface BacktestReportDTO {
  symbol: string;
  start_ts_ms: number;
  end_ts_ms: number;
  initial_balance: string;
  final_balance: string;
  realized_pnl: string;
  max_drawdown: string;
  max_drawdown_pct: string;
  total_fills: number;
  winning_fills: number;
  errors: string[];
}

export const strategiesApi = {
  types: () => unwrap<StrategyType[]>(api.get("/strategies/types")),
  list: () => unwrap<StrategyDTO[]>(api.get("/strategies")),
  get: (id: number) => unwrap<StrategyDTO>(api.get(`/strategies/${id}`)),
  create: (payload: {
    name: string;
    type: string;
    params: Record<string, any>;
    stoploss: Record<string, any>;
    symbols: string[];
    is_template?: boolean;
  }) => unwrap<StrategyDTO>(api.post("/strategies", payload)),
  update: (id: number, payload: Partial<StrategyDTO>) =>
    unwrap<StrategyDTO>(api.put(`/strategies/${id}`, payload)),
  remove: (id: number) =>
    unwrap<{ deleted: boolean; id: number }>(api.delete(`/strategies/${id}`)),
  start: (id: number) => unwrap<StrategyRunDTO>(api.post(`/strategies/${id}/start`)),
  stopRun: (id: number, rid: number) =>
    unwrap<StrategyRunDTO>(api.post(`/strategies/${id}/runs/${rid}/stop`)),
  runs: (id: number, limit = 50) =>
    unwrap<StrategyRunDTO[]>(
      api.get(`/strategies/${id}/runs`, { params: { limit } }),
    ),
  allRuns: (limit = 100) =>
    unwrap<StrategyRunDTO[]>(api.get(`/strategies/runs`, { params: { limit } })),
  backtest: (payload: {
    strategy_type: string;
    params: Record<string, any>;
    stoploss: Record<string, any>;
    symbols: string[];
    interval: string;
    start_ms: number;
    end_ms: number;
    initial_balance?: string;
  }) => unwrap<BacktestReportDTO>(api.post("/strategies/backtest", payload)),
};
