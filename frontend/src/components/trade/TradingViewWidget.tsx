import { useEffect, useRef } from "react";

/** TradingView symbol 映射：BTCUSDT → BINANCE:BTCUSDT.P (U 本位永续). */
function mapSymbol(symbol: string): string {
  const s = symbol.toUpperCase();
  // 简化：所有 U 本位 USDT 永续都加 .P
  if (s.endsWith("USDT") || s.endsWith("USDC") || s.endsWith("BUSD")) {
    return `BINANCE:${s}.P`;
  }
  // 币本位 XXXUSD_PERP 等直接透传
  return `BINANCE:${s}`;
}

declare global {
  interface Window {
    TradingView: any;
    __tvLoadingPromise?: Promise<void>;
  }
}

function loadTvScript(): Promise<void> {
  if (window.TradingView) return Promise.resolve();
  if (window.__tvLoadingPromise) return window.__tvLoadingPromise;
  window.__tvLoadingPromise = new Promise((resolve, reject) => {
    const s = document.createElement("script");
    s.src = "https://s3.tradingview.com/tv.js";
    s.async = true;
    s.onload = () => resolve();
    s.onerror = () => reject(new Error("TradingView script load failed"));
    document.head.appendChild(s);
  });
  return window.__tvLoadingPromise;
}

export default function TradingViewWidget({
  symbol,
  interval = "15",
  height = 520,
}: {
  symbol: string;
  interval?: string;
  height?: number;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const widgetRef = useRef<any>(null);
  // 每次 symbol/interval 变化给一个新容器 id，避免 widget 残留
  const containerId = `tv_${symbol.toLowerCase()}_${interval}`;

  useEffect(() => {
    let canceled = false;
    loadTvScript()
      .then(() => {
        if (canceled || !containerRef.current) return;
        // 清空容器
        containerRef.current.innerHTML = "";
        const div = document.createElement("div");
        div.id = containerId;
        div.style.width = "100%";
        div.style.height = "100%";
        containerRef.current.appendChild(div);

        widgetRef.current = new window.TradingView.widget({
          symbol: mapSymbol(symbol),
          interval,
          container_id: containerId,
          autosize: true,
          theme: "dark",
          style: "1",
          locale: "zh_CN",
          toolbar_bg: "#0b0e14",
          enable_publishing: false,
          allow_symbol_change: false,
          studies: [],
          hide_side_toolbar: false,
          timezone: "Etc/UTC",
        });
      })
      .catch((e) => {
        if (containerRef.current) {
          containerRef.current.innerHTML =
            '<div class="text-xs text-accent-down p-4">TradingView 加载失败：' +
            (e?.message || "unknown") +
            "</div>";
        }
      });
    return () => {
      canceled = true;
      widgetRef.current = null;
      if (containerRef.current) containerRef.current.innerHTML = "";
    };
  }, [symbol, interval, containerId]);

  return (
    <div
      ref={containerRef}
      className="card overflow-hidden"
      style={{ height }}
    />
  );
}
