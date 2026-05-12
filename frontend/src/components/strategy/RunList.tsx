import { useEffect, useState } from "react";
import { strategiesApi, StrategyRunDTO } from "@/api/strategies";
import { ApiError } from "@/api/client";

export default function RunList({ strategyId }: { strategyId: number }) {
  const [runs, setRuns] = useState<StrategyRunDTO[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);

  async function refresh() {
    try {
      const list = await strategiesApi.runs(strategyId, 20);
      setRuns(list);
    } catch {
      // 静默
    }
  }

  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, 3000);
    return () => window.clearInterval(t);
  }, [strategyId]);

  async function stop(rid: number) {
    setBusy("stop-" + rid);
    try {
      await strategiesApi.stopRun(strategyId, rid);
      await refresh();
    } catch (e) {
      setErr(e instanceof ApiError ? e.detail : String(e));
    } finally {
      setBusy(null);
    }
  }

  if (runs.length === 0) {
    return <div className="text-xs text-text-muted">该策略暂无运行记录</div>;
  }

  return (
    <div className="flex flex-col gap-1">
      <table className="w-full text-xs">
        <thead className="text-text-muted">
          <tr className="text-left">
            <th className="py-1">Run</th>
            <th>状态</th>
            <th>启动</th>
            <th>心跳</th>
            <th>成交</th>
            <th>SL</th>
            <th className="text-right"></th>
          </tr>
        </thead>
        <tbody>
          {runs.map((r) => (
            <tr key={r.id} className="border-t border-border">
              <td className="py-1">#{r.id}</td>
              <td>
                <span className={statusClass(r.status)}>{r.status}</span>
              </td>
              <td className="text-text-muted">
                {r.started_at ? r.started_at.slice(11, 19) : "—"}
              </td>
              <td className="text-text-muted">
                {r.last_heartbeat ? r.last_heartbeat.slice(11, 19) : "—"}
              </td>
              <td className="num">{r.stats?.orders_filled ?? 0}</td>
              <td className="num">{r.stats?.stop_loss_triggered ?? 0}</td>
              <td className="text-right">
                {(r.status === "running" || r.status === "stale") && (
                  <button
                    className="btn text-[10px]"
                    disabled={busy === "stop-" + r.id}
                    onClick={() => stop(r.id)}
                  >
                    停止
                  </button>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {/* 最近一次的 last_error */}
      {runs[0]?.last_error && (
        <div className="text-[11px] text-accent-down break-all bg-accent-down/10 p-2 rounded">
          最近错误：{runs[0].last_error}
        </div>
      )}
      {err && <div className="text-accent-down text-xs">{err}</div>}
    </div>
  );
}

function statusClass(s: string): string {
  switch (s) {
    case "running":
      return "tag text-accent-up border-accent-up/40";
    case "stale":
      return "tag text-accent-warn border-accent-warn/40";
    case "error":
      return "tag text-accent-down border-accent-down/40";
    case "stopped":
      return "tag text-text-muted";
    default:
      return "tag";
  }
}
