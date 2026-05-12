import { useEffect, useState } from "react";
import { marketApi, DepthDTO } from "@/api/market";
import { useMarketStore } from "@/stores/market";

/** 订单簿：轮询 REST /market/depth?limit=20 每 1s；币安 WS bookTicker 用于顶部快照. */
export default function OrderBook() {
  const symbol = useMarketStore((s) => s.currentSymbol);
  const [depth, setDepth] = useState<DepthDTO | null>(null);

  useEffect(() => {
    let cancelled = false;
    const fetchOnce = async () => {
      try {
        const d = await marketApi.depth(symbol, 15);
        if (!cancelled) setDepth(d);
      } catch {
        if (!cancelled) setDepth(null);
      }
    };
    fetchOnce();
    const t = window.setInterval(fetchOnce, 1000);
    return () => {
      cancelled = true;
      window.clearInterval(t);
    };
  }, [symbol]);

  const bids = depth?.bids ?? [];
  const asks = depth?.asks ?? [];

  // 计算最大数量用于条形图
  const maxQty = Math.max(
    1,
    ...bids.slice(0, 10).map(([, q]) => Number(q)),
    ...asks.slice(0, 10).map(([, q]) => Number(q)),
  );

  return (
    <div className="card p-2 text-[11px] flex flex-col">
      <div className="flex justify-between px-1 mb-1 text-text-muted">
        <span>价</span>
        <span>数量</span>
      </div>
      {/* asks 倒序显示（卖一在底） */}
      <div className="flex flex-col-reverse">
        {asks.slice(0, 10).map(([p, q], i) => (
          <Row key={"a" + i} price={p} qty={q} side="ask" maxQty={maxQty} />
        ))}
      </div>
      {/* 中间分隔线 */}
      <div className="border-t border-border my-1" />
      <div className="flex flex-col">
        {bids.slice(0, 10).map(([p, q], i) => (
          <Row key={"b" + i} price={p} qty={q} side="bid" maxQty={maxQty} />
        ))}
      </div>
    </div>
  );
}

function Row({
  price,
  qty,
  side,
  maxQty,
}: {
  price: string;
  qty: string;
  side: "bid" | "ask";
  maxQty: number;
}) {
  const pct = Math.min(100, (Number(qty) / maxQty) * 100);
  const barColor = side === "bid" ? "bg-accent-up/10" : "bg-accent-down/10";
  const txtColor = side === "bid" ? "text-accent-up" : "text-accent-down";
  return (
    <div className="relative flex justify-between px-1 py-0.5 num">
      <div
        className={"absolute inset-y-0 right-0 " + barColor}
        style={{ width: `${pct}%` }}
      />
      <span className={"relative " + txtColor}>{Number(price).toFixed(2)}</span>
      <span className="relative text-text-soft">{Number(qty).toFixed(3)}</span>
    </div>
  );
}
