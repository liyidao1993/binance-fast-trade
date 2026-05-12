"""运行时状态容器（进程内全局，可变）.

main.py 的 lifespan 负责初始化；其它模块用 ``from app import runtime`` 读取.
把它独立出来能避免循环 import，也方便单测里替换 mock.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .exchanges.binance import BinanceClient
    from .exchanges.binance_ws import BinanceWsClient
    from .security import Cipher


# Fernet 加密器（登录成功后由 app.security 从 PIN 派生并写入这里）
cipher: "Cipher | None" = None

# Binance REST 客户端（API Key 解密后初始化）
binance_client: "BinanceClient | None" = None

# Binance WS 客户端（W2 起使用）
binance_ws: "BinanceWsClient | None" = None

# 进程内 tick 价缓存：{ "BTCUSDT": {"bid": "50000.1", "ask": "50000.2", "ts": 1699999999} }
# 由 binance_ws 的 bookTicker 订阅实时写入；floating.py 读取
tickers: dict[str, dict[str, str]] = {}

# 进程内追价 task 表：{ client_order_id: asyncio.Task }
chase_tasks: dict[str, "asyncio.Task"] = {}


def reset() -> None:
    """登出或关闭时清理（不负责 await，调用方处理）."""
    global cipher, binance_client, binance_ws
    cipher = None
    binance_client = None
    binance_ws = None
    tickers.clear()
    # 取消所有追价 task
    for t in list(chase_tasks.values()):
        if not t.done():
            t.cancel()
    chase_tasks.clear()
