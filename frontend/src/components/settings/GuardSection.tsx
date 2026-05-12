import { useEffect, useState } from "react";
import { GuardRules, guardApi } from "@/api/guard";
import { ApiError } from "@/api/client";

const requirePinOptions = [
  "close-all",
  "reverse",
  "mainnet-switch",
  "key-delete",
];

export default function GuardSection() {
  const [rules, setRules] = useState<GuardRules | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function refresh() {
    try {
      const r = await guardApi.get();
      setRules(r);
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.detail : String(e) });
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function save() {
    if (!rules) return;
    setBusy(true);
    setMsg(null);
    try {
      await guardApi.put(rules);
      setMsg({ ok: true, text: "护栏已保存（仅影响新订单与新启动的策略）" });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.detail : String(e) });
    } finally {
      setBusy(false);
    }
  }

  async function reset() {
    if (!window.confirm("恢复默认护栏？")) return;
    setBusy(true);
    try {
      const r = await guardApi.reset();
      setRules(r);
      setMsg({ ok: true, text: "已恢复默认护栏" });
    } catch (e) {
      setMsg({ ok: false, text: e instanceof ApiError ? e.detail : String(e) });
    } finally {
      setBusy(false);
    }
  }

  if (!rules) {
    return (
      <div className="card p-4 text-xs text-text-muted">加载护栏配置中…</div>
    );
  }

  const setR = (patch: Partial<GuardRules>) => setRules({ ...rules, ...patch });

  return (
    <div className="card p-4 flex flex-col gap-3">
      <div className="flex items-center">
        <div className="font-semibold">硬性护栏（非自动熔断）</div>
        <button className="btn ml-auto" onClick={reset}>
          恢复默认
        </button>
      </div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="label">单笔上限 USDT</label>
          <input
            className="input num"
            type="number"
            min={0}
            value={rules.per_order_max_usdt}
            onChange={(e) =>
              setR({ per_order_max_usdt: Number(e.target.value) })
            }
          />
        </div>
        <div>
          <label className="label">总仓上限 USDT</label>
          <input
            className="input num"
            type="number"
            min={0}
            value={rules.global_max_position_usdt}
            onChange={(e) =>
              setR({ global_max_position_usdt: Number(e.target.value) })
            }
          />
        </div>
        <div>
          <label className="label">最高杠杆</label>
          <input
            className="input num"
            type="number"
            min={1}
            max={125}
            value={rules.global_max_leverage}
            onChange={(e) =>
              setR({ global_max_leverage: Number(e.target.value) })
            }
          />
        </div>
        <div>
          <label className="label">AI 审查默认</label>
          <select
            className="input"
            value={rules.ai_review_default}
            onChange={(e) => setR({ ai_review_default: e.target.value })}
          >
            <option value="off">off</option>
            <option value="weak">weak</option>
            <option value="strong">strong</option>
          </select>
        </div>
        <div className="col-span-2">
          <label className="label">allowed_symbols（逗号分隔，空=允许所有）</label>
          <input
            className="input"
            value={rules.allowed_symbols.join(",")}
            onChange={(e) =>
              setR({
                allowed_symbols: e.target.value
                  .split(/[,\s]+/)
                  .map((s) => s.trim().toUpperCase())
                  .filter(Boolean),
              })
            }
          />
        </div>
        <div className="col-span-2">
          <label className="label">forbidden_symbols（逗号分隔）</label>
          <input
            className="input"
            value={rules.forbidden_symbols.join(",")}
            onChange={(e) =>
              setR({
                forbidden_symbols: e.target.value
                  .split(/[,\s]+/)
                  .map((s) => s.trim().toUpperCase())
                  .filter(Boolean),
              })
            }
          />
        </div>
        <div className="col-span-2">
          <label className="label">破坏性操作要求 PIN</label>
          <div className="flex flex-wrap gap-3">
            {requirePinOptions.map((opt) => (
              <label key={opt} className="flex items-center gap-1 text-xs">
                <input
                  type="checkbox"
                  checked={rules.require_pin_for.includes(opt)}
                  onChange={(e) => {
                    const set = new Set(rules.require_pin_for);
                    if (e.target.checked) set.add(opt);
                    else set.delete(opt);
                    setR({ require_pin_for: Array.from(set) });
                  }}
                />
                <span>{opt}</span>
              </label>
            ))}
          </div>
        </div>
      </div>

      <button className="btn-primary" disabled={busy} onClick={save}>
        {busy ? "保存中…" : "保存护栏"}
      </button>

      {msg && (
        <div
          className={
            "text-xs " + (msg.ok ? "text-accent-up" : "text-accent-down")
          }
        >
          {msg.text}
        </div>
      )}

      <div className="text-[11px] text-text-muted leading-relaxed">
        · V2 风控设计：<b>没有</b>日亏损熔断和自动暂停策略；风险控制收敛到每个策略的强制 SL + 每笔订单的 SL + 手动 Kill Switch。
        <br />
        · 护栏只做"硬性防手抖"：单笔/总仓/杠杆/白黑名单 + 破坏性操作 PIN。
      </div>
    </div>
  );
}
