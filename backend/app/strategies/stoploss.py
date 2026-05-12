"""★ 策略级止损（W3 核心）.

V2 §一 风控铁律：**每个策略实例强制配置 SL**。
本模块提供三种模式的统一数据结构与价格计算：
    1. percent       固定百分比     SL = entry * (1 - pct)        （做多）
    2. atr           ATR 跟踪       SL = entry - n * ATR(period)
    3. fixed_amount  固定亏损金额    根据当前杠杆反推价位

runner.start() 必须调 :func:`validate_stoploss_config` 校验；校验不过直接拒绝启动。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal

StopLossMode = Literal["percent", "atr", "fixed_amount"]


# ------------------------------------------------------------------
# 配置对象
# ------------------------------------------------------------------


@dataclass
class StopLossConfig:
    """策略级止损配置.

    :param mode:              SL 模式
    :param pct:               mode=percent 必填；正数 0.003 = 0.3%
    :param atr_period:        mode=atr 必填；ATR 周期（K 线数）
    :param atr_multiplier:    mode=atr 必填；n × ATR 的 n
    :param max_loss_usdt:     mode=fixed_amount 必填；最大亏损 USDT
    :param trailing:          是否跟踪止损（仅 percent / atr 支持）
    :param action_on_trigger: 触发后动作；W3 固定 "market_close"（平仓）
    """

    mode: StopLossMode
    pct: Decimal | None = None
    atr_period: int | None = None
    atr_multiplier: Decimal | None = None
    max_loss_usdt: Decimal | None = None
    trailing: bool = False
    action_on_trigger: Literal["market_close"] = "market_close"

    # 可选：每档独立 SL（grid 等策略）
    per_ladder: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "pct": None if self.pct is None else str(self.pct),
            "atr_period": self.atr_period,
            "atr_multiplier": None if self.atr_multiplier is None else str(self.atr_multiplier),
            "max_loss_usdt": None if self.max_loss_usdt is None else str(self.max_loss_usdt),
            "trailing": self.trailing,
            "action_on_trigger": self.action_on_trigger,
            "per_ladder": self.per_ladder,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "StopLossConfig":
        if not isinstance(d, dict):
            raise ValueError("stoploss 必须是 dict")
        mode = d.get("mode")
        if mode not in ("percent", "atr", "fixed_amount"):
            raise ValueError(f"未知 SL 模式：{mode}")
        return cls(
            mode=mode,  # type: ignore[arg-type]
            pct=_to_dec(d.get("pct")),
            atr_period=None if d.get("atr_period") is None else int(d["atr_period"]),
            atr_multiplier=_to_dec(d.get("atr_multiplier")),
            max_loss_usdt=_to_dec(d.get("max_loss_usdt")),
            trailing=bool(d.get("trailing", False)),
            action_on_trigger=d.get("action_on_trigger", "market_close"),
            per_ladder=bool(d.get("per_ladder", False)),
        )


def _to_dec(v: Any) -> Decimal | None:
    if v is None or v == "":
        return None
    return Decimal(str(v))


# ------------------------------------------------------------------
# 校验
# ------------------------------------------------------------------


def validate_stoploss_config(cfg: StopLossConfig) -> list[str]:
    """返回错误消息列表；空列表 = 合法."""
    errs: list[str] = []

    if cfg.mode == "percent":
        if cfg.pct is None:
            errs.append("mode=percent 需要 pct")
        elif cfg.pct <= 0:
            errs.append(f"pct 必须 > 0，当前 {cfg.pct}")
        elif cfg.pct >= Decimal("1"):
            errs.append(f"pct 应该用小数（0.01 = 1%），当前 {cfg.pct} 像是百分数")

    elif cfg.mode == "atr":
        if not cfg.atr_period or cfg.atr_period <= 1:
            errs.append("mode=atr 需要 atr_period > 1")
        if cfg.atr_multiplier is None or cfg.atr_multiplier <= 0:
            errs.append("mode=atr 需要 atr_multiplier > 0")

    elif cfg.mode == "fixed_amount":
        if cfg.max_loss_usdt is None or cfg.max_loss_usdt <= 0:
            errs.append("mode=fixed_amount 需要 max_loss_usdt > 0")

    if cfg.trailing and cfg.mode == "fixed_amount":
        errs.append("fixed_amount 不支持 trailing")

    return errs


def require_valid_stoploss(cfg_raw: Any) -> StopLossConfig:
    """把 JSON 对象解析为 StopLossConfig 并做强校验。不通过抛 ValueError.

    供 runner.start() / 策略 CRUD 在启动前调用。
    """
    if cfg_raw is None:
        raise ValueError("策略必须配置止损 (stoploss)")
    cfg = StopLossConfig.from_dict(cfg_raw) if isinstance(cfg_raw, dict) else cfg_raw
    errs = validate_stoploss_config(cfg)
    if errs:
        raise ValueError("stoploss 配置非法：" + "; ".join(errs))
    return cfg


# ------------------------------------------------------------------
# 价格计算
# ------------------------------------------------------------------


def calc_stop_price(
    cfg: StopLossConfig,
    *,
    entry_price: Decimal,
    side: str,  # "BUY"（long）/ "SELL"（short）
    atr: Decimal | None = None,
    quantity: Decimal | None = None,
    leverage: int | None = None,
) -> Decimal:
    """按配置计算止损价.

    :raises ValueError: 缺少必须字段（如 percent 没填 pct）或数值不合法
    """
    if entry_price <= 0:
        raise ValueError("entry_price 必须 > 0")

    long_pos = side.upper() == "BUY"
    sign = Decimal("-1") if long_pos else Decimal("1")  # 亏损方向

    if cfg.mode == "percent":
        if cfg.pct is None:
            raise ValueError("percent 缺少 pct")
        return entry_price * (Decimal("1") + sign * cfg.pct)

    if cfg.mode == "atr":
        if cfg.atr_multiplier is None:
            raise ValueError("atr 缺少 multiplier")
        if atr is None or atr <= 0:
            raise ValueError("atr 值无效（需要调用方传入）")
        return entry_price + sign * cfg.atr_multiplier * atr

    if cfg.mode == "fixed_amount":
        if cfg.max_loss_usdt is None or quantity is None or quantity <= 0:
            raise ValueError("fixed_amount 需要 max_loss_usdt 和 quantity")
        # 亏损 = |entry - stop| × quantity = max_loss
        # stop = entry + sign * max_loss / quantity    （不涉及杠杆，因为现货名义价也是 entry*qty）
        price_delta = cfg.max_loss_usdt / quantity
        return entry_price + sign * price_delta

    raise ValueError(f"未知 SL 模式：{cfg.mode}")


# ------------------------------------------------------------------
# ATR 计算（给 atr 模式的策略用，例如 ma_cross）
# ------------------------------------------------------------------


def compute_atr(
    highs: list[Decimal],
    lows: list[Decimal],
    closes: list[Decimal],
    period: int,
) -> Decimal:
    """Wilder ATR.

    需要至少 ``period + 1`` 个数据点；否则返回 0。
    """
    n = len(closes)
    if n < period + 1 or len(highs) != n or len(lows) != n:
        return Decimal("0")

    # True Range
    trs: list[Decimal] = []
    for i in range(1, n):
        h, lo, pc = highs[i], lows[i], closes[i - 1]
        tr = max(h - lo, abs(h - pc), abs(lo - pc))
        trs.append(tr)

    # Wilder 的平滑：第 1 个 ATR = 前 period 个 TR 的均值；之后 = (prev*(p-1)+tr)/p
    if len(trs) < period:
        return Decimal("0")
    atr = sum(trs[:period], start=Decimal("0")) / Decimal(period)
    for tr in trs[period:]:
        atr = (atr * Decimal(period - 1) + tr) / Decimal(period)
    return atr
