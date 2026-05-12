import { useMemo, useState } from "react";
import SchemaForm, {
  defaultsFromSchema,
  pruneEmpty,
  SchemaValue,
} from "./SchemaForm";
import {
  BacktestReportDTO,
  StrategyType,
  strategiesApi,
} from "@/api/strategies";
import { ApiError } from "@/api/client";

export default function BacktestPanel({ types }: { types: StrategyType[] }) {
  const [typeKey, setTypeKey] = useState(types[0]?.type_key ?? "");
  const currentType = useMemo(
    () => types.find((t) => t.type_key === typeKey),
    [types, typeKey],
  );
  const [params, setParams] = useState<SchemaValue>(() =>
    defaultsFromSchema(currentType?.params_schema),
  );
  const [stoploss, setStoploss] = useState<SchemaValue>(() =>
    defaultsFromSchema(currentType?.stoploss_schema),
  );
  const [symbol, setSymbol] = useState("BTCUSDT");
  const [interval, setInterval] = useState("15m");
  const [days, setDays] = useState(7);
  const [initialBalance, setInitialBalance] = useState("10000");

  const [running, setRunning] = useState(false);
  const [report, setReport] = useState<BacktestReportDTO | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function run() {
    if (!currentType) return;
    setRunning(true);
    setErr(null);
    setReport(null);
    try {
      const now = Date.now();
      const start = now - days * 24 * 3600 * 1000;
      const r = await strategiesApi.backtest({
        strategy_type: typeKey,
        params: pruneEmpty(params),
        stoploss: pruneEmpty(stoploss),
        symbols: [symbol.toUpperCase()],
        interval,
        start_ms: start,
        end_ms: now,
        initial_balance: initialBalance,
      });
      setReport(r);
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="card p-4 flex flex-col gap-3">
      <div className="font-semibold">回测</div>

      <div className="grid grid-cols-2 gap-2">
        <div>
          <label className="label">策略类型</label>
          <select
            className="input"
            value={typeKey}
            onChange={(e) => {
              setTypeKey(e.target.value);
              const t = types.find((x) => x.type_key === e.target.value);
              setParams(defaultsFromSchema(t?.params_schema));
              setStoploss(defaultsFromSchema(t?.stoploss_schema));
            }}
          >
            {types.map((t) => (
              <option key={t.type_key} value={t.type_key}>
                {t.display_name}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">Symbol</label>
          <input
            className="input"
            value={symbol}
            onChange={(e) => setSymbol(e.target.value)}
          />
        </div>
        <div>
          <label className="label">K 线周期</label>
          <select
            className="input"
            value={interval}
            onChange={(e) => setInterval(e.target.value)}
          >
            {["1m", "5m", "15m", "30m", "1h", "4h", "1d"].map((iv) => (
              <option key={iv} value={iv}>
                {iv}
              </option>
            ))}
          </select>
        </div>
        <div>
          <label className="label">回测天数</label>
          <input
            className="input num"
            type="number"
            min={1}
            max={365}
            value={days}
            onChange={(e) => setDays(Number(e.target.value))}
          />
        </div>
        <div className="col-span-2">
          <label className="label">初始余额 (USDT)</label>
          <input
            className="input num"
            value={initialBalance}
            onChange={(e) => setInitialBalance(e.target.value)}
          />
        </div>
      </div>

      {currentType && (
        <>
          <div>
            <div className="text-xs text-accent-info mb-1">策略参数</div>
            <SchemaForm
              schema={currentType.params_schema}
              value={params}
              onChange={setParams}
            />
          </div>
          <div>
            <div className="text-xs text-accent-warn mb-1">止损</div>
            <SchemaForm
              schema={currentType.stoploss_schema}
              value={stoploss}
              onChange={setStoploss}
            />
          </div>
        </>
      )}

      <button
        className="btn-primary"
        disabled={running}
        onClick={run}
      >
        {running ? "回测中…" : "运行回测"}
      </button>

      {err && <div className="text-accent-down text-xs break-all">{err}</div>}

      {report && (
        <div className="bg-bg-raised p-3 rounded text-xs grid grid-cols-2 gap-1">
          <Stat
            label="初始余额"
            value={Number(report.initial_balance).toFixed(2)}
          />
          <Stat
            label="最终余额"
            value={Number(report.final_balance).toFixed(2)}
          />
          <Stat
            label="已实现 PnL"
            value={Number(report.realized_pnl).toFixed(2)}
            color={Number(report.realized_pnl) >= 0 ? "up" : "down"}
          />
          <Stat
            label="最大回撤"
            value={`${Number(report.max_drawdown).toFixed(2)} (${Number(report.max_drawdown_pct).toFixed(2)}%)`}
            color="warn"
          />
          <Stat label="成交笔数" value={String(report.total_fills)} />
          <Stat
            label="盈利笔数"
            value={`${report.winning_fills} / ${report.total_fills}`}
          />
          {report.errors.length > 0 && (
            <div className="col-span-2 text-accent-down mt-1">
              错误：{report.errors.slice(0, 3).join(" | ")}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function Stat({
  label,
  value,
  color,
}: {
  label: string;
  value: string;
  color?: "up" | "down" | "warn";
}) {
  const cls =
    color === "up"
      ? "text-accent-up"
      : color === "down"
        ? "text-accent-down"
        : color === "warn"
          ? "text-accent-warn"
          : "";
  return (
    <div className="flex justify-between">
      <span className="text-text-muted">{label}</span>
      <span className={"num " + cls}>{value}</span>
    </div>
  );
}
