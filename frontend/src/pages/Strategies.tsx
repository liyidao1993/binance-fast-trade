import { useEffect, useState } from "react";
import {
  StrategyDTO,
  StrategyType,
  strategiesApi,
} from "@/api/strategies";
import { ApiError } from "@/api/client";
import StrategyEditor from "@/components/strategy/StrategyEditor";
import RunList from "@/components/strategy/RunList";
import BacktestPanel from "@/components/strategy/BacktestPanel";

type View = "list" | "new" | "edit" | "backtest";

export default function StrategiesPage() {
  const [strategies, setStrategies] = useState<StrategyDTO[]>([]);
  const [types, setTypes] = useState<StrategyType[]>([]);
  const [view, setView] = useState<View>("list");
  const [editing, setEditing] = useState<StrategyDTO | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function refresh() {
    try {
      const [list, tps] = await Promise.all([
        strategiesApi.list(),
        strategiesApi.types(),
      ]);
      setStrategies(list);
      setTypes(tps);
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    }
  }

  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, 5000);
    return () => window.clearInterval(t);
  }, []);

  async function startStrategy(s: StrategyDTO) {
    setBusy("start-" + s.id);
    setErr(null);
    try {
      await strategiesApi.start(s.id);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  async function removeStrategy(s: StrategyDTO) {
    if (!window.confirm(`确认删除策略 "${s.name}"？运行中的 run 会被停止。`)) return;
    setBusy("del-" + s.id);
    setErr(null);
    try {
      await strategiesApi.remove(s.id);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (view === "new" || view === "edit") {
    return (
      <StrategyEditor
        types={types}
        editing={view === "edit" ? editing : null}
        onClose={(saved) => {
          setView("list");
          setEditing(null);
          if (saved) refresh();
        }}
      />
    );
  }

  if (view === "backtest") {
    return (
      <div className="flex flex-col gap-3">
        <button
          className="btn w-fit"
          onClick={() => setView("list")}
        >
          ← 返回列表
        </button>
        <BacktestPanel types={types} />
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex gap-2">
        <button className="btn-primary" onClick={() => setView("new")}>
          + 新建策略
        </button>
        <button className="btn" onClick={() => setView("backtest")}>
          回测
        </button>
        <button className="btn ml-auto" onClick={refresh}>
          刷新
        </button>
      </div>

      {err && <div className="text-accent-down text-xs break-all">{err}</div>}

      {strategies.length === 0 ? (
        <div className="card p-6 text-center text-text-muted text-sm">
          还没有策略。点击 <span className="text-accent-info">新建策略</span> 开始。
        </div>
      ) : (
        <div className="flex flex-col gap-3">
          {strategies.map((s) => (
            <div key={s.id} className="card p-3">
              <div className="flex items-center gap-2 mb-2">
                <div className="font-semibold text-sm">{s.name}</div>
                <span className="tag">{s.type}</span>
                <span className="tag">symbols: {s.symbols.join(",")}</span>
                {s.running_count > 0 && (
                  <span className="tag text-accent-up border-accent-up/40">
                    running × {s.running_count}
                  </span>
                )}
                <div className="ml-auto flex gap-1">
                  <button
                    className="btn text-xs"
                    onClick={() => {
                      setEditing(s);
                      setView("edit");
                    }}
                  >
                    编辑
                  </button>
                  <button
                    className="btn-up text-xs"
                    disabled={busy === "start-" + s.id}
                    onClick={() => startStrategy(s)}
                  >
                    启动
                  </button>
                  <button
                    className="btn-down text-xs"
                    disabled={busy === "del-" + s.id}
                    onClick={() => removeStrategy(s)}
                  >
                    删除
                  </button>
                </div>
              </div>

              <details className="mb-2">
                <summary className="text-xs text-text-muted cursor-pointer">
                  配置详情
                </summary>
                <div className="mt-1 grid grid-cols-2 gap-2 text-[11px]">
                  <pre className="bg-bg p-2 rounded overflow-auto">
                    {JSON.stringify({ params: s.params }, null, 2)}
                  </pre>
                  <pre className="bg-bg p-2 rounded overflow-auto">
                    {JSON.stringify({ stoploss: s.stoploss }, null, 2)}
                  </pre>
                </div>
              </details>

              <RunList strategyId={s.id} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
