import { useEffect, useState } from "react";
import { marketApi, PositionRowDTO } from "@/api/market";
import { ordersApi } from "@/api/orders";
import { ApiError } from "@/api/client";

function fmtPnl(v: string) {
  const n = Number(v);
  const cls = n >= 0 ? "text-accent-up" : "text-accent-down";
  return <span className={"num " + cls}>{n.toFixed(2)}</span>;
}

export default function PositionTable() {
  const [rows, setRows] = useState<PositionRowDTO[]>([]);
  const [openOrders, setOpenOrders] = useState<any[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function refresh() {
    try {
      const p = await marketApi.positions();
      setRows(p.open);
      const o = await ordersApi.listOpen();
      setOpenOrders(o);
    } catch {
      // 静默
    }
  }

  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, 3000);
    return () => window.clearInterval(t);
  }, []);

  async function closePct(pos: PositionRowDTO, pct: number) {
    setErr(null);
    setBusy(pos.symbol + "-close-" + pct);
    try {
      const qty = Math.abs(Number(pos.positionAmt)) * pct;
      await ordersApi.floating({
        symbol: pos.symbol,
        side: Number(pos.positionAmt) > 0 ? "long" : "short",
        action: "close",
        float_pct: "0",
        qty_mode: "contract",
        qty: qty.toString(),
        reduce_only: true,
      });
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function cancelOne(o: any) {
    setBusy("cancel-" + o.orderId);
    try {
      await ordersApi.cancel(o.symbol, o.clientOrderId, o.orderId);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div className="card p-3 flex flex-col gap-3">
      <section>
        <div className="flex justify-between items-center mb-1">
          <div className="font-semibold text-sm">持仓 ({rows.length})</div>
          <button className="tag cursor-pointer" onClick={refresh}>
            刷新
          </button>
        </div>
        {rows.length === 0 ? (
          <div className="text-xs text-text-muted py-2">无持仓</div>
        ) : (
          <table className="w-full text-xs">
            <thead className="text-text-muted">
              <tr className="text-left">
                <th className="py-1">Symbol</th>
                <th>数量</th>
                <th>入场</th>
                <th>标记</th>
                <th>未实现</th>
                <th className="text-right">操作</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.symbol} className="border-t border-border">
                  <td className="py-1">
                    {r.symbol}{" "}
                    <span
                      className={
                        "tag " +
                        (Number(r.positionAmt) > 0
                          ? "text-accent-up"
                          : "text-accent-down")
                      }
                    >
                      {Number(r.positionAmt) > 0 ? "LONG" : "SHORT"}
                    </span>
                  </td>
                  <td className="num">
                    {Math.abs(Number(r.positionAmt)).toFixed(3)}
                  </td>
                  <td className="num">{Number(r.entryPrice).toFixed(2)}</td>
                  <td className="num">{Number(r.markPrice).toFixed(2)}</td>
                  <td>{fmtPnl(r.unRealizedProfit)}</td>
                  <td className="text-right">
                    <div className="flex gap-1 justify-end">
                      {[0.25, 0.5, 1].map((pct) => (
                        <button
                          key={pct}
                          className="btn text-[10px]"
                          disabled={busy === r.symbol + "-close-" + pct}
                          onClick={() => closePct(r, pct)}
                        >
                          {pct === 1 ? "全平" : `${pct * 100}%`}
                        </button>
                      ))}
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section>
        <div className="font-semibold text-sm mb-1">
          活跃委托 ({openOrders.length})
        </div>
        {openOrders.length === 0 ? (
          <div className="text-xs text-text-muted py-2">无活跃单</div>
        ) : (
          <table className="w-full text-xs">
            <thead className="text-text-muted">
              <tr className="text-left">
                <th className="py-1">Symbol</th>
                <th>方向</th>
                <th>类型</th>
                <th>价格</th>
                <th>数量</th>
                <th className="text-right"></th>
              </tr>
            </thead>
            <tbody>
              {openOrders.slice(0, 50).map((o) => (
                <tr key={o.orderId} className="border-t border-border">
                  <td className="py-1">{o.symbol}</td>
                  <td
                    className={
                      o.side === "BUY" ? "text-accent-up" : "text-accent-down"
                    }
                  >
                    {o.side}
                  </td>
                  <td className="text-text-muted">{o.type}</td>
                  <td className="num">{o.price}</td>
                  <td className="num">{o.origQty}</td>
                  <td className="text-right">
                    <button
                      className="btn text-[10px]"
                      disabled={busy === "cancel-" + o.orderId}
                      onClick={() => cancelOne(o)}
                    >
                      撤
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      {err && <div className="text-accent-down text-xs break-all">{err}</div>}
    </div>
  );
}
