import { useState, useEffect } from "react";
import { useNavigate } from "react-router-dom";
import { useAuthStore } from "@/stores/auth";

export default function LoginPage() {
  const navigate = useNavigate();
  const { initialized, authenticated, login, init, loading, error, refreshStatus, env } =
    useAuthStore();
  const [pin, setPin] = useState("");
  const [localErr, setLocalErr] = useState<string | null>(null);

  useEffect(() => {
    refreshStatus();
  }, [refreshStatus]);

  useEffect(() => {
    if (authenticated) navigate("/trade", { replace: true });
  }, [authenticated, navigate]);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    setLocalErr(null);
    try {
      if (!initialized) {
        await init(pin);
      } else {
        await login(pin);
      }
      navigate("/trade", { replace: true });
    } catch (err: any) {
      setLocalErr(err?.message || "失败");
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <form
        onSubmit={onSubmit}
        className="card p-6 w-full max-w-sm flex flex-col gap-4"
      >
        <div>
          <div className="text-lg font-semibold">Binance Fast Trade</div>
          <div className="text-xs text-text-muted mt-1">
            环境：
            <span
              className={
                env === "mainnet"
                  ? "text-accent-down font-semibold"
                  : "text-accent-info"
              }
            >
              {env}
            </span>
          </div>
        </div>

        <div>
          <label className="label">
            {initialized ? "输入 PIN 登录" : "设置 PIN（首次初始化）"}
          </label>
          <input
            className="input"
            type="password"
            value={pin}
            autoFocus
            onChange={(e) => setPin(e.target.value)}
            placeholder="至少 4 位"
            minLength={4}
            maxLength={64}
            required
          />
        </div>

        {(error || localErr) && (
          <div className="text-accent-down text-xs">{localErr || error}</div>
        )}

        <button
          className="btn-primary py-2"
          type="submit"
          disabled={loading || pin.length < 4}
        >
          {loading ? "处理中…" : initialized ? "登录" : "初始化并登录"}
        </button>

        <div className="text-[11px] text-text-muted leading-relaxed">
          · 登录会派生密钥解密 API Key
          <br />
          · 会话 cookie 8 小时有效
          <br />
          · 忘记 PIN 需删除 data/app.db 重新初始化
        </div>
      </form>
    </div>
  );
}
