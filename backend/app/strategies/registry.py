"""策略类注册中心.

用装饰器自动注册策略类；runner / API 通过 type_key 查找类并实例化.
"""

from __future__ import annotations

from typing import TypeVar

from .base import BaseStrategy

T = TypeVar("T", bound=type[BaseStrategy])


class StrategyRegistry:
    """全局单例."""

    def __init__(self) -> None:
        self._types: dict[str, type[BaseStrategy]] = {}

    def register(self, cls: T) -> T:
        if not issubclass(cls, BaseStrategy):
            raise TypeError("仅能注册 BaseStrategy 子类")
        key = getattr(cls, "type_key", "") or ""
        if not key:
            raise ValueError(f"{cls.__name__}.type_key 未设置")
        if key in self._types:
            raise ValueError(f"策略 type_key={key} 已注册")
        self._types[key] = cls
        return cls

    def get(self, key: str) -> type[BaseStrategy]:
        try:
            return self._types[key]
        except KeyError as e:
            raise KeyError(f"未注册的策略 type：{key}") from e

    def has(self, key: str) -> bool:
        return key in self._types

    def all_types(self) -> list[dict]:
        return [
            {
                "type_key": k,
                "display_name": getattr(cls, "display_name", k),
                "params_schema": cls.params_schema(),
                "stoploss_schema": cls.stoploss_schema(),
            }
            for k, cls in sorted(self._types.items())
        ]


# 单例
registry = StrategyRegistry()


def register_strategy(cls: T) -> T:
    """装饰器：``@register_strategy`` 挂在 BaseStrategy 子类上."""
    return registry.register(cls)
