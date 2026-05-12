import { useState } from "react";
import { useMarketStore } from "@/stores/market";
import { ordersApi, FloatingOrderIn } from "@/api/orders";
import { ApiError } from "@/api/client";

type Side = "long" | "short";
type Action = "open" | "close";
type QtyMode = "usdt" | "contract";

interface Form {
  side: Side;
  action: Action;
  float_pct: number; // 百分比数值（用户视角）：-2 ~ +2
  qty_mode: QtyMode;
  qty: string;
  chase_enabled: boolean;
  chase_interval: number;
  chase_max_attempts: number;
  chase_on_expire: "cancel" | "market";
  ladders_count: number;
  ladders_spacing_pct: number; // 相对 mid 的 %，例如 0.05 表示 0.05%
  tp_pct: string; // 百分比数值
  sl_pct: string;
  reduce_only: boolean;
  leverage: string; // 空=不改
  time_in_force: "GTC" | "IOC" | "FOK" | "GTX";
}

const emptyForm: Form = {
  side: "long",
  action: "open",
  float_pct: -0.1,
  qty_mode: "usdt",
  qty: "100",
  chase_enabled: false,
  chase_interval: 5,
  chase_max_attempts: 3,
  chase_on_expire: "cancel",
  ladders_count: 1,
  ladders_spacing_pct: 0.05,
  tp_pct: "",
  sl_pct: "",
  reduce_only: false,
  leverage: "",
  time_in_force: "GTC",
};

export default function FloatingOrderPanel() {
  const symbol = useMarketStore((s) => s.currentSymbol);
  const tickers = useMarketStore((s) => s.tickers);
  const tk = tickers[symbol];

  const bid = tk ? Number(tk.bid) : 0;
  const ask = tk ? Number(tk.ask) : 0;
  const mid = bid && ask ? (bid + ask) / 2 : 0;

  const [form, setForm] = useState<Form>(emptyForm);
  const [advanced, setAdvanced] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const targetPrice =
    mid > 0 ? mid * (1 + form.float_pct / 100) : 0;

  function set<K extends keyof Form>(k: K, v: Form[K]) {
    setForm((f) => ({ ...f, [k]: v }));
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setErr(null);
    setResult(null);
    setSubmitting(true);

    // 组装 payload
    const payload: FloatingOrderIn = {
      symbol,
      side: form.side,
      action: form.action,
      float_pct: (form.float_pct / 100).toString(), // 用户 -0.1% → 后端 -0.001
      qty_mode: form.qty_mode,
      qty: form.qty,
      chase: {
        enabled: form.chase_enabled,
        interval_sec: form.chase_interval,
        max_attempts: form.chase_max_attempts,
        on_expire: form.chase_on_expire,
      },
      ladders: {
        count: form.ladders_count,
        spacing_pct: (form.ladders_spacing_pct / 100).toString(),
      },
      tp_pct: form.tp_pct ? (Number(form.tp_pct) / 100).toString() : null,
      sl_pct: form.sl_pct ? (Number(form.sl_pct) / 100).toString() : null,
      reduce_only: form.reduce_only || form.action === "close",
      ai_review: "off",
      leverage: form.leverage ? Number(form.leverage) : null,
      time_in_force: form.time_in_force,
    };

    try {
      const r = await ordersApi.floating(payload);
      const placed = r.ladders.filter((l) => l.exchange_order_id).length;
      setResult(
        `已下 ${placed}/${r.ladders.length} 档，mid=${r.mid_price}` +
          (r.chase_started ? "，追价已启动" : ""),
      );
    } catch (e) {
      const msg = e instanceof ApiError ? e.detail : String(e);
      setErr(msg);
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <form onSubmit={submit} className="card p-3 flex flex-col gap-3">
      {/* 当前价 */}
      <div className="flex items-baseline justify-between">
        <div>
          <div className="text-xs text-text-muted">币安实时 mid（决策用）</div>
          <div className="text-2xl font-semibold num">
            {mid > 0 ? mid.toFixed(2) : "—"}
          </div>
        </div>
        <div className="text-xs text-text-muted text-right leading-tight">
          bid <span className="text-accent-up num">{bid.toFixed(2)}</span>
          <br />
          ask <span className="text-accent-down num">{ask.toFixed(2)}</span>
        </div>
      </div>

      {/* 方向 + 开平 */}
      <div className="grid grid-cols-2 gap-2">
        <div className="grid grid-cols-2 gap-1">
          <button
            type="button"
            onClick={() => set("side", "long")}
            className={form.side === "long" ? "btn-up" : "btn"}
          >
            多
          </button>
          <button
            type="button"
            onClick={() => set("side", "short")}
            className={form.side === "short" ? "btn-down" : "btn"}
          >
            空
          </button>
        </div>
        <div className="grid grid-cols-2 gap-1">
          <button
            type="button"
            onClick={() => set("action", "open")}
            className={form.action === "open" ? "btn-primary" : "btn"}
          >
            开
          </button>
          <button
            type="button"
            onClick={() => set("action", "close")}
            className={form.action === "close" ? "btn-primary" : "btn"}
          >
            平
          </button>
        </div>
      </div>

      {/* 数量 */}
      <div>
        <div className="flex items-center gap-2 mb-1">
          <label className="label mb-0">数量</label>
          <div className="ml-auto flex gap-1">
            <button
              type="button"
              className={form.qty_mode === "usdt" ? "tag text-accent-info" : "tag"}
              onClick={() => set("qty_mode", "usdt")}
            >
              USDT
            </button>
            <button
              type="button"
              className={
                form.qty_mode === "contract" ? "tag text-accent-info" : "tag"
              }
              onClick={() => set("qty_mode", "contract")}
            >
              合约张数
            </button>
          </div>
        </div>
        <input
          className="input num"
          value={form.qty}
          onChange={(e) => set("qty", e.target.value)}
          inputMode="decimal"
        />
      </div>

      {/* 浮动百分比 */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="label mb-0">浮动 % (相对 mid)</label>
          <span
            className={
              "num text-sm " +
              (form.float_pct < 0
                ? "text-accent-up"
                : form.float_pct > 0
                  ? "text-accent-down"
                  : "")
            }
          >
            {form.float_pct.toFixed(2)}% → {targetPrice > 0 ? targetPrice.toFixed(2) : "—"}
          </span>
        </div>
        <input
          type="range"
          min={-2}
          max={2}
          step={0.01}
          value={form.float_pct}
          onChange={(e) => set("float_pct", Number(e.target.value))}
          className="w-full"
        />
        <div className="flex justify-between text-[11px] text-text-muted">
          <span>-2%</span>
          <span>0</span>
          <span>+2%</span>
        </div>
      </div>

      {/* 高级 */}
      <button
        type="button"
        className="text-xs text-accent-info w-fit"
        onClick={() => setAdvanced((v) => !v)}
      >
        {advanced ? "▼ 高级（收起）" : "▶ 高级（追价 / 分档 / TP/SL / 杠杆）"}
      </button>
      {advanced && (
        <div className="grid grid-cols-2 gap-2 text-xs">
          <label className="flex items-center gap-2 col-span-2">
            <input
              type="checkbox"
              checked={form.chase_enabled}
              onChange={(e) => set("chase_enabled", e.target.checked)}
            />
            <span>启用追价</span>
          </label>
          {form.chase_enabled && (
            <>
              <div>
                <label className="label">间隔秒</label>
                <input
                  className="input num"
                  type="number"
                  min={0.5}
                  max={300}
                  step={0.5}
                  value={form.chase_interval}
                  onChange={(e) =>
                    set("chase_interval", Number(e.target.value))
                  }
                />
              </div>
              <div>
                <label className="label">最多追价次数</label>
                <input
                  className="input num"
                  type="number"
                  min={0}
                  max={50}
                  value={form.chase_max_attempts}
                  onChange={(e) =>
                    set("chase_max_attempts", Number(e.target.value))
                  }
                />
              </div>
              <div className="col-span-2">
                <label className="label">到期处理</label>
                <div className="flex gap-1">
                  <button
                    type="button"
                    className={form.chase_on_expire === "cancel" ? "btn-primary" : "btn"}
                    onClick={() => set("chase_on_expire", "cancel")}
                  >
                    撤单
                  </button>
                  <button
                    type="button"
                    className={form.chase_on_expire === "market" ? "btn-warn" : "btn"}
                    onClick={() => set("chase_on_expire", "market")}
                  >
                    转市价
                  </button>
                </div>
              </div>
            </>
          )}
          <div>
            <label className="label">分档数</label>
            <input
              className="input num"
              type="number"
              min={1}
              max={20}
              value={form.ladders_count}
              onChange={(e) => set("ladders_count", Number(e.target.value))}
            />
          </div>
          <div>
            <label className="label">档间距 %</label>
            <input
              className="input num"
              type="number"
              step={0.01}
              value={form.ladders_spacing_pct}
              onChange={(e) =>
                set("ladders_spacing_pct", Number(e.target.value))
              }
            />
          </div>
          <div>
            <label className="label">止盈 %</label>
            <input
              className="input num"
              type="number"
              step={0.01}
              value={form.tp_pct}
              placeholder="可空"
              onChange={(e) => set("tp_pct", e.target.value)}
            />
          </div>
          <div>
            <label className="label">止损 %</label>
            <input
              className="input num"
              type="number"
              step={0.01}
              value={form.sl_pct}
              placeholder="可空"
              onChange={(e) => set("sl_pct", e.target.value)}
            />
          </div>
          <div>
            <label className="label">杠杆</label>
            <input
              className="input num"
              type="number"
              min={1}
              max={125}
              value={form.leverage}
              placeholder="不改留空"
              onChange={(e) => set("leverage", e.target.value)}
            />
          </div>
          <div>
            <label className="label">TIF</label>
            <select
              className="input"
              value={form.time_in_force}
              onChange={(e) =>
                set("time_in_force", e.target.value as Form["time_in_force"])
              }
            >
              <option value="GTC">GTC</option>
              <option value="IOC">IOC</option>
              <option value="FOK">FOK</option>
              <option value="GTX">GTX (postOnly)</option>
            </select>
          </div>
          <label className="flex items-center gap-2 col-span-2">
            <input
              type="checkbox"
              checked={form.reduce_only}
              onChange={(e) => set("reduce_only", e.target.checked)}
            />
            <span>仅减仓 (reduceOnly)</span>
          </label>
        </div>
      )}

      {/* 提交 */}
      <button
        type="submit"
        disabled={submitting || mid <= 0}
        className={
          form.side === "long"
            ? "btn-up py-2"
            : "btn-down py-2"
        }
      >
        {submitting
          ? "下单中…"
          : `${form.side === "long" ? "买入" : "卖出"} ${form.action === "open" ? "开仓" : "平仓"} @ ${targetPrice > 0 ? targetPrice.toFixed(2) : "—"}`}
      </button>

      {result && <div className="text-accent-up text-xs">{result}</div>}
      {err && <div className="text-accent-down text-xs break-all">{err}</div>}
    </form>
  );
}
