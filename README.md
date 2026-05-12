# 币安合约快速交易系统 (binance-fast-trade)

> 单人自用的**快速交易执行器 + 策略跑马灯 + AI 助手**。
> 详见根目录上层的 `开发计划V2.txt`。

## 里程碑状态
- ✅ W1 骨架 + Binance Testnet 连通 + PIN 登录 + 最简限价单 + 精度单测
- ✅ W2 浮动挂单 + 追价 + 护栏 + 一键全平 + TP/SL 挂钩
- ✅ W3 策略框架（强制 SL）+ 网格 + EMA + AI 信号占位 + 回测
- ✅ W4 前端完整面板（TradingView + 浮动下单 + 订单簿 + 持仓 + 策略 + 回测 + Settings）+ /ws 实时推送
- ⏳ W5 AI 对内对外（Review + Chat + Signal）+ MCP Server
- ⏳ W6 移动响应式打磨 + TG 告警 + mainnet checklist + Caddy 部署

## 快速开始（本地开发）

### 后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"

copy ..\.env.example ..\.env
# 打开 .env 设 APP_PIN=你自定义的 PIN

pytest               # 期望 100 个测试全过
uvicorn app.main:app --reload   # http://127.0.0.1:8000/docs
```

### 前端

```powershell
cd frontend
npm install
npm run dev          # http://127.0.0.1:5173
```

前端 dev server 会把 `/api` 自动代理到 `127.0.0.1:8000`（含 WebSocket）。

### 一键登录流程（浏览器访问 http://127.0.0.1:5173）

1. 打开登录页输入 PIN（首次即初始化）
2. Settings 页录入 Binance testnet API Key
3. 刷新/重登 → 能在 Trade 页看到 TradingView 图 + 币安实时 mid + 订单簿
4. 浮动挂单面板下一笔最小额 → PositionTable 看到持仓

## Docker 部署

```powershell
docker compose up --build -d
# 前端：http://127.0.0.1:8080
# 后端（调试）：http://127.0.0.1:8000
```

## 核心设计铁律（V2 §一）
1. 交易延迟 > 其它一切——手动下单直连币安，不走队列、不走 AI 阻塞
2. Testnet 优先，mainnet 是显式开关
3. 够用就好——无 Celery / K8s / 多用户 / RBAC
4. 核心逻辑必须可测（精度 / 浮动 / 止损 / 护栏）
5. AI 是助手不是主宰——超时/失败永远降级放行

**风控取舍**：无日亏损熔断、无自动暂停策略；收敛到每策略强制 SL + 每笔订单 SL + 手动一键全平。

## 目录结构

```
binance-fast-trade/
├── backend/            FastAPI + SQLite + asyncio
│   └── app/
│       ├── api/        auth / keys / market / orders / risk(guard) / strategies / account / ws
│       ├── exchanges/  binance REST + WS（bookTicker + user stream + kline 动态订阅）
│       ├── trading/    floating / chase / killswitch / guard / positions / fills
│       ├── strategies/ base / context / runner / stoploss / backtest / impls(grid|ma_cross|ai_signal)
│       ├── tests/      100 个用例
│       ├── config.py / db.py / main.py / runtime.py / security.py
├── frontend/           Vite + React 18 + TS + Tailwind + Zustand
│   └── src/
│       ├── api/        axios 封装 + WS 客户端
│       ├── stores/     auth / market / orders
│       ├── pages/      Login / Trade / Strategies / Settings
│       └── components/ layout / trade(6) / strategy(4) / settings(2)
├── deploy/             Caddyfile（W6）
├── docker-compose.yml
└── 上级 开发计划V2.txt
```

## 数据边界

| 用途 | 来源 | 说明 |
|---|---|---|
| 挂单的 tick 价 | 币安 WS `bookTicker` | 进程内 tickers dict 实时刷新 |
| 深度 | 币安 WS `@depth` / REST `/depth` | 前端订单簿 |
| K 线（策略 + 回测） | 币安 WS `@kline_*` + REST `/klines` | 策略 on_kline 消费的是币安数据 |
| 用户流（订单/持仓/SL 挂钩） | 币安 WS `listenKey` | 唯一真源 |
| TradingView 图 | TV 自己的行情源 | **仅肉眼参考**，不参与任何决策 |

**原则**：TV 显示价和币安 WS 极短时差时，以币安 WS 为准。

## 测试与验证

```powershell
cd backend && pytest              # 后端 100 个单测
cd frontend && npm run build      # 前端 TS 严格 + Vite 生产构建
```
