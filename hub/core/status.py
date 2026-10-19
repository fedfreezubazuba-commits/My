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
    "paused": ("На паузе", GREY),
    "quarantine": ("Карантин", YELLOW),
}

COLORS = {GREEN: "#2e7d32", YELLOW: "#c77700", RED: "#c62828", GREY: "#757575", BLUE: "#1565c0"}
DOTS = {GREEN: "●", YELLOW: "●", RED: "●", GREY: "○", BLUE: "●"}
PROBLEM = {"delayed", "error", "login", "quarantine", "retry"}


def word(status) -> str:
    return STATUSES.get(status, (status or "—", GREY))[0]


def color(status) -> str:
    return STATUSES.get(status, ("", GREY))[1]


def effective(job, state) -> str:
    if not job.get("enabled", True):
        return "paused"
    return (state or {}).get("status") or "new"
