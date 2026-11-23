"""Provider registry and selection."""

from __future__ import annotations

from typing import Callable, Optional

from app.data.providers.base import (
    Capabilities,
    Provider,
    ProviderError,
    ProviderInfo,
    ProviderUnavailable,
    board_of,
)
from app.data.providers.eastmoney import EastmoneyProvider
from app.data.providers.sina import SinaProvider
from app.data.providers.tushare import TushareProvider

__all__ = [
    "Capabilities",
    "Provider",
    "ProviderError",
    "ProviderInfo",
    "ProviderUnavailable",
    "board_of",
    "TushareProvider",
    "SinaProvider",
    "EastmoneyProvider",
    "get_provider",
    "list_providers",
    "provider_for",
    "candidates",
]

ALIASES = {
    "tushare": "tushare",
    "ts": "tushare",
    "sina": "sina",
    "sinafinance": "sina",
    "eastmoney": "eastmoney",
    "em": "eastmoney",
    "akshare": "akshare",
    "ak": "akshare",
}

# Capability -> providers that can serve it, most authoritative first.
# History puts Sina ahead of Eastmoney on purpose: Eastmoney's push2his endpoint
# starts refusing connections after a few dozen symbols, which is useless for a
# 6500-symbol sweep. Sina serves the same raw prices reliably (no factors).
CAPABILITY_ORDER = {
    "calendar": ("tushare", "akshare"),
    "universe": ("tushare", "sina"),
    "history": ("tushare", "sina", "eastmoney"),
    "adj_factors": ("tushare", "eastmoney"),
    "daily_basic": ("tushare", "sina"),
    "spot": ("sina", "tushare"),
}


def _akshare_class():
    from app.data.providers.akshare import AkShareProvider

    return AkShareProvider


def get_provider(name: str, **kwargs) -> Provider:
    key = ALIASES.get((name or "").strip().lower())
    if key is None:
        raise ProviderError(f"未知数据源：{name}（可选 tushare / sina / eastmoney / akshare）")
    if key == "akshare":
        return _akshare_class()(**kwargs)
    factory: Callable[..., Provider] = {
        "tushare": TushareProvider,
        "sina": SinaProvider,
        "eastmoney": EastmoneyProvider,
    }[key]
    return factory(**kwargs)


def list_providers() -> list[dict]:
    entries = []
    for name in ("tushare", "sina", "eastmoney", "akshare"):
        try:
            provider = get_provider(name)
        except ProviderError as exc:  # pragma: no cover - defensive
            entries.append({"name": name, "available": False, "reason": str(exc)})
            continue
        ok, reason = provider.available()
        info: ProviderInfo = provider.info()
        entries.append(
            {
                "name": info.name,
                "label": info.label,
                "available": ok,
                "reason": reason,
                "note": info.note,
                "capabilities": info.capabilities.__dict__,
            }
        )
    return entries


def candidates(capability: str, prefer: Optional[str] = None) -> list[Provider]:
    """Every available provider that declares ``capability``, best first."""
    names: list[str] = list(CAPABILITY_ORDER.get(capability, ()))
    if prefer and prefer.strip().lower() not in {"", "auto"}:
        key = ALIASES.get(prefer.strip().lower(), prefer.strip().lower())
        names = [key] + [name for name in names if name != key]

    ordered: list[Provider] = []
    for name in names:
        try:
            provider = get_provider(name)
        except ProviderError:
            continue
        if not getattr(provider.capabilities, capability, False):
            continue
        ok, _ = provider.available()
        if ok:
            ordered.append(provider)
    return ordered


def provider_for(capability: str, prefer: Optional[str] = None) -> Provider:
    """First available provider that declares ``capability``."""
    ordered = candidates(capability, prefer)
    if not ordered:
        raise ProviderUnavailable(f"没有可用的数据源提供 {capability}")
    return ordered[0]
