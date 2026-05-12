import { useEffect, useState } from "react";
import { ApiKeyDTO, keysApi } from "@/api/keys";
import { ApiError } from "@/api/client";
import { useAuthStore } from "@/stores/auth";

export default function KeysSection() {
  const env = useAuthStore((s) => s.env);
  const [list, setList] = useState<ApiKeyDTO[]>([]);
  const [form, setForm] = useState({
    api_key: "",
    api_secret: "",
    label: "",
    env: env,
  });
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<{ ok: boolean; text: string } | null>(null);

  async function refresh() {
    try {
      const l = await keysApi.list();
      setList(l);
    } catch {
      // 静默
    }
  }

  useEffect(() => {
    refresh();
  }, []);

  async function save() {
    setBusy(true);
    setMsg(null);
    try {
      await keysApi.save({
        api_key: form.api_key,
        api_secret: form.api_secret,
        label: form.label,
        env: form.env as "testnet" | "mainnet",
      });
      setMsg({ ok: true, text: "保存成功。Binance 客户端已热重建。" });
      setForm({ api_key: "", api_secret: "", label: "", env: form.env });
      await refresh();
    } catch (e) {
      setMsg({
        ok: false,
        text: e instanceof ApiError ? e.detail : String(e),
      });
    } finally {
      setBusy(false);
    }
  }

  async function remove(keyEnv: "testnet" | "mainnet") {
    if (!window.confirm(`删除 ${keyEnv} 环境的 API Key？`)) return;
    setBusy(true);
    setMsg(null);
    try {
      await keysApi.remove(keyEnv);
      setMsg({ ok: true, text: `${keyEnv} Key 已删除` });
      await refresh();
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
    <div className="card p-4 flex flex-col gap-3">
      <div className="font-semibold">API Key</div>

      {/* 当前已配 */}
      {list.length === 0 ? (
        <div className="text-xs text-text-muted">未配置 API Key。</div>
      ) : (
        <table className="w-full text-xs">
          <thead className="text-text-muted">
            <tr className="text-left">
              <th className="py-1">环境</th>
              <th>Label</th>
              <th>Key (掩码)</th>
              <th>创建时间</th>
              <th className="text-right"></th>
            </tr>
          </thead>
          <tbody>
            {list.map((k) => (
              <tr key={k.id} className="border-t border-border">
                <td className="py-1">
                  <span
                    className={
                      "tag " +
                      (k.env === "mainnet"
                        ? "text-accent-down border-accent-down/40"
                        : "text-accent-info border-accent-info/40")
                    }
                  >
                    {k.env}
                  </span>
                </td>
                <td>{k.label || "—"}</td>
                <td className="num text-text-muted">{k.api_key_masked}</td>
                <td className="text-text-muted">
                  {k.created_at.slice(0, 19).replace("T", " ")}
                </td>
                <td className="text-right">
                  <button
                    className="btn-down text-[10px]"
                    onClick={() => remove(k.env)}
                  >
                    删除
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}

      {/* 新建/更新表单 */}
      <div className="border-t border-border pt-3">
        <div className="text-sm font-semibold mb-2">
          添加 / 更新 API Key
        </div>
        <div className="grid grid-cols-2 gap-2">
          <div>
            <label className="label">环境</label>
            <select
              className="input"
              value={form.env}
              onChange={(e) =>
                setForm({ ...form, env: e.target.value as "testnet" | "mainnet" })
              }
            >
              <option value="testnet">testnet</option>
              <option value="mainnet">mainnet</option>
            </select>
          </div>
          <div>
            <label className="label">Label (可空)</label>
            <input
              className="input"
              value={form.label}
              onChange={(e) => setForm({ ...form, label: e.target.value })}
              placeholder="e.g. 主账户"
            />
          </div>
          <div className="col-span-2">
            <label className="label">API Key</label>
            <input
              className="input"
              value={form.api_key}
              onChange={(e) => setForm({ ...form, api_key: e.target.value })}
              autoComplete="off"
            />
          </div>
          <div className="col-span-2">
            <label className="label">API Secret</label>
            <input
              className="input"
              type="password"
              value={form.api_secret}
              onChange={(e) => setForm({ ...form, api_secret: e.target.value })}
              autoComplete="off"
            />
          </div>
        </div>
        <button
          className="btn-primary mt-3 w-full"
          disabled={busy || !form.api_key || !form.api_secret}
          onClick={save}
        >
          {busy ? "保存中…" : "保存"}
        </button>
      </div>

      {msg && (
        <div
          className={
            "text-xs " + (msg.ok ? "text-accent-up" : "text-accent-down")
          }
        >
          {msg.text}
        </div>
      )}

      {form.env === "mainnet" && (
        <div className="text-[11px] text-accent-warn bg-accent-warn/10 p-2 rounded">
          ⚠ mainnet 密钥请仅开合约交易权限 + 关闭提现 + IP 白名单
        </div>
      )}
    </div>
  );
}
