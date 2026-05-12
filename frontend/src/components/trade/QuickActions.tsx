import { useState } from "react";
import { ordersApi } from "@/api/orders";
import { useMarketStore } from "@/stores/market";
import { ApiError } from "@/api/client";

/** 一键全平 / 一键反手：都要 PIN 二次确认. */
export default function QuickActions() {
  const symbol = useMarketStore((s) => s.currentSymbol);
  const [action, setAction] = useState<null | "closeAll" | "reverse">(null);
  const [pin, setPin] = useState("");
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function confirm() {
    if (!action) return;
    setBusy(true);
    setMsg(null);
    try {
      if (action === "closeAll") {
        await ordersApi.closeAll(pin);
        setMsg({ ok: true, text: "一键全平已执行" });
      } else {
        await ordersApi.reverse(pin, symbol);
        setMsg({ ok: true, text: `${symbol} 一键反手已执行` });
      }
      setAction(null);
      setPin("");
    } catch (e) {
      setMsg({
        ok: false,
        text: e instanceof ApiError ? e.detail : String(e),
      });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="card p-3">
      <div className="flex gap-2 mb-2">
        <button className="btn-warn flex-1" onClick={() => setAction("reverse")}>
          一键反手 ({symbol})
        </button>
        <button className="btn-down flex-1" onClick={() => setAction("closeAll")}>
          一键全平
        </button>
      </div>

      {action && (
        <div className="flex flex-col gap-2 bg-bg-raised p-2 rounded-md">
          <div className="text-xs">
            {action === "closeAll"
              ? "将撤销所有活跃单并市价平所有持仓"
              : `将撤销 ${symbol} 所有单，市价平该 symbol 持仓，再按等量反向开仓`}
            ，需要 PIN 确认：
          </div>
          <input
            className="input"
            type="password"
            autoFocus
            value={pin}
            onChange={(e) => setPin(e.target.value)}
            placeholder="PIN"
          />
          <div className="flex gap-1">
            <button
              className="btn flex-1"
              onClick={() => {
                setAction(null);
                setPin("");
              }}
              disabled={busy}
            >
              取消
            </button>
            <button
              className="btn-down flex-1"
              onClick={confirm}
              disabled={busy || pin.length < 4}
            >
              {busy ? "执行中…" : "确认执行"}
            </button>
          </div>
        </div>
      )}

      {msg && (
        <div
          className={
            "text-xs mt-2 " + (msg.ok ? "text-accent-up" : "text-accent-down")
          }
        >
          {msg.text}
        </div>
      )}
    </div>
  );
}
