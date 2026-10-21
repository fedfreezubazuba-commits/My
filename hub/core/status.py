"""Статусы показателей: светофор и слово (раздел 11 ТЗ)."""

GREEN, YELLOW, RED, GREY, BLUE = "green", "yellow", "red", "grey", "blue"

STATUSES = {
    "new": ("Ещё не загружался", GREY),
    "updated": ("Обновлено", GREEN),
    "unchanged": ("Без изменений", GREEN),
    "waiting": ("Ждём новые данные (в срок)", GREEN),
    "delayed": ("Задерживается", YELLOW),
    "retry": ("Сбой, повторю позже", YELLOW),
    "error": ("Ошибка", RED),
    "login": ("Нужен вход", RED),
    "blocked": ("Сайт не пускает — пауза", YELLOW),
    "paused": ("На паузе", GREY),
    "quarantine": ("Карантин", YELLOW),
}

COLORS = {GREEN: "#2e7d32", YELLOW: "#c77700", RED: "#c62828", GREY: "#757575", BLUE: "#1565c0"}
DOTS = {GREEN: "●", YELLOW: "●", RED: "●", GREY: "○", BLUE: "●"}
PROBLEM = {"delayed", "error", "login", "quarantine", "retry", "blocked"}


def word(status) -> str:
    return STATUSES.get(status, (status or "—", GREY))[0]


def color(status) -> str:
    return STATUSES.get(status, ("", GREY))[1]


def effective(job, state) -> str:
    if not job.get("enabled", True):
        return "paused"
    return (state or {}).get("status") or "new"


def explain(job, state) -> str:
    """Что значит статус показателя и что делать — простыми словами (панель под списком, «История»)."""
    from . import errors as E
    from .base_fmt import fmt, fmt_date
    st = state or {}
    s = effective(job, st)
    msg = " ".join(str(st.get("message") or "").split())
    msg = msg if len(msg) <= 400 else msg[:400] + "…"
    exp = fmt_date((st.get("expected_next") or "")[:10]) if st.get("expected_next") else ""
    last = ""
    if st.get("last_value") is not None:
        last = (f"Последнее значение {fmt(st['last_value'])} {st.get('last_unit') or ''}".rstrip()
                + (f" за {st['last_label']}" if st.get("last_label") else "") + ". ")
    what, todo, _a = E.CATEGORIES.get(st.get("category") or E.INTERNAL, E.CATEGORIES[E.INTERNAL])
    text = {
        "new": "Ещё не загружался. Нажмите «Обновить сейчас».",
        "updated": f"Всё в порядке: получены новые данные. {last}"
                   + (f"Следующие ожидаем около {exp}." if exp else ""),
        "unchanged": f"Всё в порядке: проверили, новых данных нет. {last}",
        "waiting": f"Всё в порядке: источник ещё не опубликовал новые данные — это нормально. {last}"
                   + (f"Ожидаем около {exp}." if exp else ""),
        "delayed": f"Источник опаздывает: новые данные ожидались около {exp or '—'}, но их ещё нет. Это не ошибка — "
                   "хаб проверяет каждый день. Если опоздание большое, возможно, источник перестал обновлять "
                   f"показатель или сменил график. {last}",
        "retry": f"Временный сбой: {msg or what}. Хаб повторит сам, делать ничего не нужно.",
        "error": f"Ошибка: {msg or what}. Что делать: {todo}.",
        "login": f"Сайт требует вход: {msg or what}. Что делать: «Найти данные → Сайт → Войти на сайт…».",
        "quarantine": f"Новые данные отложены и в модели не попали: {msg or what}. Проверьте их: "
                      "«Ещё ▾ → Принять / отклонить отложенные данные» (или «Посмотреть» на «Обзоре»).",
        "paused": "На паузе — хаб не проверяет этот показатель. Включить: «Пауза / продолжить».",
        "blocked": f"{msg or what}. Так бывает, если к сайту было много запросов, — обычно ограничение снимается "
                   "само через несколько часов. Делать ничего не нужно. Если на «Обзоре» сайт по-прежнему на паузе, "
                   "можно нажать там «Открыть сайт»: если сайт покажет проверку «я не робот» — пройдите её и закройте "
                   "окно, хаб сразу попробует снова.",
    }.get(s, msg)
    if st.get("release_next") or st.get("release_note"):
        text += " Источник о сроках: " + "; ".join(x for x in (
            f"следующее обновление {fmt_date(st['release_next'][:10])}" if st.get("release_next") else "",
            st.get("release_note") or "") if x) + "."
    return " ".join(text.split())
