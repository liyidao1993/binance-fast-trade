import { api, unwrap } from "./client";

export interface SymbolInfoDTO {
  symbol: string;
  tickSize: string;
  stepSize: string;
  minQty: string;
  minNotional: string;
  pricePrecision: number;
  quantityPrecision: number;
}

export interface BookTicker {
  symbol: string;
  bidPrice: string;
  askPrice: string;
  bidQty: string;
  askQty: string;
  time: number;
}

export interface DepthDTO {
  lastUpdateId: number;
  bids: [string, string][];
  asks: [string, string][];
}

export interface PositionRowDTO {
  symbol: string;
  positionAmt: string;
  entryPrice: string;
  markPrice: string;
  unRealizedProfit: string;
  liquidationPrice: string;
  leverage: string;
  positionSide: string;
}

export const marketApi = {
  symbols: () => unwrap<SymbolInfoDTO[]>(api.get("/market/symbols")),
  ticker: (symbol: string) =>
    unwrap<BookTicker>(api.get("/market/ticker", { params: { symbol } })),
  klines: (symbol: string, interval = "15m", limit = 500) =>
    unwrap<(string | number)[][]>(
      api.get("/market/klines", { params: { symbol, interval, limit } }),
    ),
  depth: (symbol: string, limit = 20) =>
    unwrap<DepthDTO>(api.get("/market/depth", { params: { symbol, limit } })),
  positions: (symbol?: string) =>
    unwrap<{ all: PositionRowDTO[]; open: PositionRowDTO[] }>(
      api.get("/market/positions", { params: symbol ? { symbol } : undefined }),
    ),
  balance: () => unwrap<any[]>(api.get("/market/balance")),
};
