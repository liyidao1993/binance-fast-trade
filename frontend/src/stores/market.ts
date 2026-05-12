import { create } from "zustand";

export interface TickerEntry {
  bid: string;
  ask: string;
  ts: string;
}

interface MarketState {
  tickers: Record<string, TickerEntry>;
  currentSymbol: string;
  setCurrentSymbol: (s: string) => void;
  updateTicker: (symbol: string, tk: TickerEntry) => void;
}

export const useMarketStore = create<MarketState>((set) => ({
  tickers: {},
  currentSymbol: "BTCUSDT",
  setCurrentSymbol: (s) => set({ currentSymbol: s.toUpperCase() }),
  updateTicker: (symbol, tk) =>
    set((state) => ({
      tickers: {
        ...state.tickers,
        [symbol.toUpperCase()]: {
          bid: String(tk.bid ?? "0"),
          ask: String(tk.ask ?? "0"),
          ts: String(tk.ts ?? Date.now()),
        },
      },
    })),
}));

export function selectMid(sym: string) {
  return (state: MarketState): number => {
    const tk = state.tickers[sym.toUpperCase()];
    if (!tk) return 0;
    const b = Number(tk.bid);
    const a = Number(tk.ask);
    if (!b || !a) return 0;
    return (b + a) / 2;
  };
}
