"""
Профили сайтов — особые правила извлечения для конкретных магазинов и порталов.

Профиль — модуль с переменными DOMAINS (список доменов), FIELDS ({ключ: (название, единицы)})
и функцией extract(html_text, doc) -> {ключ: значение}. Мастер «Сайт» сам применяет профиль,
если ссылка ведёт на его домен.
"""
import importlib
import pkgutil
from urllib.parse import urlparse

_PROFILES = {}


def _load():
    if _PROFILES:
        return
    for m in pkgutil.iter_modules(__path__):
        mod = importlib.import_module(f"{__name__}.{m.name}")
        for d in getattr(mod, "DOMAINS", []):
            _PROFILES[d] = mod


def for_url(url):
    _load()
    host = urlparse(url).netloc.lower().split(":")[0]
    for d, mod in _PROFILES.items():
        if host == d or host.endswith("." + d):
            return mod
    return None


def by_name(name):
    _load()
    for mod in _PROFILES.values():
        if mod.__name__.rsplit(".", 1)[-1] == name:
            return mod
    return None
