"""BaseStrategy 抽象基类.

策略作者只需要继承它并实现 :meth:`on_start` / :meth:`on_kline` 等回调。

两个 classmethod 是契约：
    - :meth:`params_schema`    返回 JSON Schema，前端据此自动渲染参数表单
    - :meth:`stoploss_schema`  **必须** 返回 JSON Schema，要求前端必填；
                              后端 runner.start 会再校验一次 StopLossConfig 合法性。

事件分发约束：
    on_tick / on_kline / on_order_update 都是 ``async``，禁止长时间阻塞，
    避免拖慢同事件循环的其它策略。内部若需要计算重的工作，请 ``asyncio.to_thread``。
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from .context import StrategyContext
from .events import KlineEvent, OrderEvent, TickEvent


class BaseStrategy(ABC):
    """策略抽象基类.

    子类持有状态（如 grid 档位列表、ma_cross 上一根 EMA），通过 ``__init__`` 接收。
    """

    # 子类覆盖。这个 type_key 用来在 registry 里注册，和 DB.strategies.type 字段对齐。
    type_key: str = ""
    display_name: str = ""

    def __init__(self, ctx: StrategyContext) -> None:
        self.ctx = ctx

    # ------------------------------------------------------------------
    # 生命周期回调
    # ------------------------------------------------------------------

    async def on_start(self) -> None:
        """启动后首次机会做 bootstrap（挂初始单等）."""
        return None

    async def on_stop(self) -> None:
        """停止时做清理（撤掉策略挂的单等）.

        默认行为：给所有 symbol 发 cancel_all（幂等）。子类可覆盖做更精细收尾。
        """
        for s in self.ctx.symbols:
            await self.ctx.cancel_all(s)

    async def on_tick(self, tick: TickEvent) -> None:  # noqa: ARG002
        return None

    async def on_kline(self, k: KlineEvent) -> None:  # noqa: ARG002
        return None

    async def on_order_update(self, o: OrderEvent) -> None:  # noqa: ARG002
        return None

    # ------------------------------------------------------------------
    # classmethod 契约
    # ------------------------------------------------------------------

    @classmethod
    @abstractmethod
    def params_schema(cls) -> dict[str, Any]:
        """返回 JSON Schema，描述策略自身参数结构."""

    @classmethod
    @abstractmethod
    def stoploss_schema(cls) -> dict[str, Any]:
        """返回 JSON Schema，前端必填；后端会再用 :func:`stoploss.require_valid_stoploss` 校验."""
