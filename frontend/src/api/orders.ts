import { api, unwrap } from "./client";

export interface LimitOrderIn {
  symbol: string;
  side: "BUY" | "SELL";
  quantity?: string;
  quote_usdt?: string;
  price: string;
  time_in_force?: "GTC" | "IOC" | "FOK" | "GTX";
  reduce_only?: boolean;
  position_side?: "BOTH" | "LONG" | "SHORT";
}

export interface FloatingOrderIn {
  symbol: string;
  side: "long" | "short";
  action: "open" | "close";
  float_pct: string;
  qty_mode?: "usdt" | "contract";
  qty: string;
  chase?: {
    enabled: boolean;
    interval_sec?: number;
    max_attempts?: number;
    expire_sec?: number | null;
    on_expire?: "cancel" | "market";
  };
  ladders?: { count: number; spacing_pct: string };
  tp_pct?: string | null;
  sl_pct?: string | null;
  reduce_only?: boolean;
  ai_review?: "off" | "weak" | "strong";
  leverage?: number | null;
  time_in_force?: "GTC" | "IOC" | "FOK" | "GTX";
}

export interface FloatingLadderResult {
  idx: number;
  client_order_id: string;
  exchange_order_id: string | null;
  price: string;
  quantity: string;
  status: string;
  error: string | null;
}

export interface FloatingOrderResult {
  mid_price: string;
  guard_warnings: string[];
  ladders: FloatingLadderResult[];
  chase_started: boolean;
}

export const ordersApi = {
  placeLimit: (payload: LimitOrderIn) =>
    unwrap<any>(api.post("/orders", payload)),
  cancel: (symbol: string, clientOrderId?: string, orderId?: number) =>
    unwrap<any>(
      api.delete("/orders", {
        data: { symbol, client_order_id: clientOrderId, order_id: orderId },
      }),
    ),
  listOpen: (symbol?: string) =>
    unwrap<any[]>(
      api.get("/orders/open", { params: symbol ? { symbol } : undefined }),
    ),
  listHistory: (symbol?: string, limit = 50) =>
    unwrap<any[]>(api.get("/orders", { params: { symbol, limit } })),
  floating: (payload: FloatingOrderIn) =>
    unwrap<FloatingOrderResult>(api.post("/orders/floating", payload)),
  closeAll: (pin: string) =>
    unwrap<any>(api.post("/orders/close-all", { pin })),
  reverse: (pin: string, symbol: string) =>
    unwrap<any>(api.post("/orders/reverse", { pin, symbol })),
};
