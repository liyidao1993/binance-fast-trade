import { create } from "zustand";

export interface OrderEventDTO {
  symbol: string;
  client_order_id: string;
  exchange_order_id: number | string;
  side: "BUY" | "SELL";
  status: string;
  exec_type: string;
  order_type: string;
  last_price: string;
  last_qty: string;
  cum_qty: string;
  orig_qty: string;
  reduce_only: boolean;
  time_ms: number;
}

interface OrdersState {
  recentEvents: OrderEventDTO[];
  onOrderEvent: (e: OrderEventDTO) => void;
  clear: () => void;
}

export const useOrdersStore = create<OrdersState>((set) => ({
  recentEvents: [],
  onOrderEvent: (e) =>
    set((state) => ({
      recentEvents: [e, ...state.recentEvents].slice(0, 50),
    })),
  clear: () => set({ recentEvents: [] }),
}));
