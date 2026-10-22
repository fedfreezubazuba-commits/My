"""
Пауза по сайту. Если сайт ответил «доступ запрещён» (403), «слишком много запросов» (429) или показал
проверку «я не робот», хаб перестаёт обращаться ко всему сайту (а не только к одному показателю):
сначала на 1 час, при повторе — на 3, 12, 24 часа. Так сайт быстрее снимает ограничение, а хаб не
усугубляет его запросами каждые 15 минут. Удачная загрузка с сайта сбрасывает счётчик.

Пользователь может не ждать: «Попробовать сейчас» или «Открыть сайт» во встроенном браузере (если там
проверка «я не робот», он проходит её сам; куки сохраняются для хаба).
"""
import datetime as dt

STEPS_MIN = [60, 180, 720, 1440]
TITLES = {"www.fedstat.ru": "ЕМИСС (fedstat.ru)", "www.cbr.ru": "Банк России (cbr.ru)",
          "stats.customs.gov.cn": "Таможня Китая (stats.customs.gov.cn)"}
SITE_URLS = {"www.fedstat.ru": "https://www.fedstat.ru/", "stats.customs.gov.cn": "http://stats.customs.gov.cn/indexEn"}


def title(host) -> str:
    return TITLES.get(host, host)


def site_url(host) -> str:
    return SITE_URLS.get(host, f"https://{host}/")


def until(store, host, now=None) -> dt.datetime | None:
    """До какого момента сайт на паузе (None — не на паузе)."""
    raw = store.meta_get(f"host_block:{host}")
    if not raw:
        return None
    try:
        t = dt.datetime.fromisoformat(raw)
    except ValueError:
        return None
    return t if t > (now or dt.datetime.now()) else None


def block(store, host, reason="", now=None) -> dt.datetime:
    now = now or dt.datetime.now()
    n = int(store.meta_get(f"host_block_n:{host}") or 0) + 1
    t = now + dt.timedelta(minutes=STEPS_MIN[min(n, len(STEPS_MIN)) - 1])
    store.meta_set(f"host_block:{host}", t.isoformat(timespec="seconds"))
    store.meta_set(f"host_block_n:{host}", str(n))
    store.meta_set(f"host_block_reason:{host}", reason or "")
    return t


def lift(store, host):
    """Снять паузу (пользователь хочет попробовать сейчас); счётчик повторов сохраняется."""
    store.meta_set(f"host_block:{host}", "")


def clear(store, host):
    """Удачная загрузка — сайт снова пускает: паузы и счётчика больше нет."""
    if store.meta_get(f"host_block_n:{host}") or store.meta_get(f"host_block:{host}"):
        for k in ("host_block", "host_block_n", "host_block_reason"):
            store.meta_set(f"{k}:{host}", "")


def active(store, now=None) -> list[tuple[str, dt.datetime, str]]:
    """[(сайт, до когда, причина)] всех сайтов на паузе."""
    out = []
    for r in store.q("SELECT key FROM meta WHERE key LIKE 'host_block:%'"):
        host = r["key"].split(":", 1)[1]
        t = until(store, host, now)
        if t:
            out.append((host, t, store.meta_get(f"host_block_reason:{host}") or ""))
    return out
