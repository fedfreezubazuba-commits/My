"""
Источники данных. Чтобы добавить источник, положите модуль в эту папку с классом —
наследником Source и заполненным `kind`; регистрация автоматическая (см. КАК_ДОБАВИТЬ_ИСТОЧНИК.md).
"""
import importlib
import pkgutil

from .base import Context, FetchResult, Obs, SeriesInfo, Source  # noqa: F401

_REGISTRY = {}
_ERRORS = {}


def _discover():
    if _REGISTRY:
        return
    for mod in pkgutil.iter_modules(__path__):
        if mod.name.startswith("_") or mod.name in ("base",) or mod.name.endswith("_api"):
            continue
        try:
            m = importlib.import_module(f"{__name__}.{mod.name}")
        except Exception as exc:  # noqa: BLE001 — сломанный модуль не должен ломать остальные
            _ERRORS[mod.name] = exc
            continue
        for obj in vars(m).values():
            if isinstance(obj, type) and issubclass(obj, Source) and obj is not Source and obj.kind:
                _REGISTRY.setdefault(obj.kind, obj)


def get(kind) -> Source:
    _discover()
    if kind not in _REGISTRY:
        raise KeyError(f"Неизвестный источник «{kind}»" + (f": {_ERRORS[kind]}" if kind in _ERRORS else ""))
    return _REGISTRY[kind]()


def all_sources() -> dict:
    _discover()
    return dict(_REGISTRY)


def load_errors() -> dict:
    _discover()
    return dict(_ERRORS)
