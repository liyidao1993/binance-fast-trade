import SymbolPicker from "@/components/trade/SymbolPicker";
import TradingViewWidget from "@/components/trade/TradingViewWidget";
import FloatingOrderPanel from "@/components/trade/FloatingOrderPanel";
import OrderBook from "@/components/trade/OrderBook";
import PositionTable from "@/components/trade/PositionTable";
import QuickActions from "@/components/trade/QuickActions";
import { useMarketStore } from "@/stores/market";

export default function TradePage() {
  const symbol = useMarketStore((s) => s.currentSymbol);

  return (
    <div className="grid gap-3 lg:grid-cols-[1fr_360px]">
      {/* 左侧主区 */}
      <div className="flex flex-col gap-3 min-w-0">
        <SymbolPicker />
        <TradingViewWidget symbol={symbol} />
        <PositionTable />
      </div>

      {/* 右侧交易区 */}
      <div className="flex flex-col gap-3 lg:max-w-[360px]">
        <FloatingOrderPanel />
        <QuickActions />
        <OrderBook />
      </div>
    </div>
  );
}
