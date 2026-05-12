import { useEffect, useState } from "react";
import { marketApi, SymbolInfoDTO } from "@/api/market";
import { useMarketStore } from "@/stores/market";
import { ws } from "@/api/ws";

/** 简易 symbol picker：搜索 + 常用收藏. */
export default function SymbolPicker() {
  const currentSymbol = useMarketStore((s) => s.currentSymbol);
  const setCurrentSymbol = useMarketStore((s) => s.setCurrentSymbol);
  const [all, setAll] = useState<SymbolInfoDTO[]>([]);
  const [q, setQ] = useState("");

  useEffect(() => {
    marketApi.symbols().then(setAll).catch(() => {});
  }, []);

  // 当前 symbol 变化时，切 WS 订阅
  useEffect(() => {
    const topic = `ticker:${currentSymbol}`;
    ws.subscribe(topic);
    return () => ws.unsubscribe(topic);
  }, [currentSymbol]);

  const favorites = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT"];
  const qUpper = q.trim().toUpperCase();
  const filtered = qUpper
    ? all.filter((s) => s.symbol.includes(qUpper)).slice(0, 20)
    : [];

  return (
    <div className="card p-3 flex flex-col gap-2">
      <div className="flex items-center gap-2">
        <div className="text-lg font-semibold">{currentSymbol}</div>
        <input
          value={q}
          onChange={(e) => setQ(e.target.value)}
          placeholder="搜 symbol…"
          className="input ml-auto w-40 text-xs"
        />
      </div>
      <div className="flex flex-wrap gap-1">
        {favorites.map((s) => (
          <button
            key={s}
            onClick={() => {
              setCurrentSymbol(s);
              setQ("");
            }}
            className={
              "tag cursor-pointer " +
              (s === currentSymbol
                ? "border-accent-info/60 text-accent-info"
                : "")
            }
          >
            {s.replace("USDT", "")}
          </button>
        ))}
      </div>
      {filtered.length > 0 && (
        <div className="border border-border rounded-md max-h-48 overflow-auto">
          {filtered.map((s) => (
            <div
              key={s.symbol}
              onClick={() => {
                setCurrentSymbol(s.symbol);
                setQ("");
              }}
              className="px-2 py-1.5 text-xs hover:bg-bg-raised cursor-pointer flex justify-between"
            >
              <span>{s.symbol}</span>
              <span className="text-text-muted">tick {s.tickSize}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
