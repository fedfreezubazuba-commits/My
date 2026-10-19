"""
Категории ошибок (раздел 12 ТЗ). У каждой — человеческое объяснение и одна кнопка действия.

Адаптеры источников поднимают HubError с категорией. Любое другое исключение
переводится в категорию функцией classify().
"""
import socket
import urllib.error

NETWORK = "network"
RATE_LIMIT = "rate_limit"
STRUCTURE = "structure"
LOGIN = "login"
BOT = "bot"
FILE_BUSY = "file_busy"
CHECK_FAILED = "check_failed"
NO_NEW_DATA = "no_new_data"
INTERNAL = "internal"

# категория -> (что случилось, что делать / подпись кнопки, код действия)
CATEGORIES = {
    NETWORK: ("Нет интернета или сайт недоступен", "Повторю автоматически", "retry"),
    RATE_LIMIT: ("Сайт ограничивает частоту запросов", "Повторю позже, ничего делать не нужно", "retry"),
    STRUCTURE: ("Изменилась структура страницы или файла", "Нужна правка — «Отправить Claude»", "claude"),
    LOGIN: ("Сайт требует вход", "Войти на сайт", "login"),
    BOT: ("Сайт включил защиту от роботов (капча)", "Открыть во встроенном браузере", "browser"),
    FILE_BUSY: ("Файл занят — вероятно, открыт в Excel", "Закройте файл, запись повторится сама", "retry"),
    CHECK_FAILED: ("Новые данные выглядят подозрительно и отложены", "Посмотреть → «Принять» или «Отклонить»", "review"),
    NO_NEW_DATA: ("Новых данных ещё нет", "Это не ошибка", None),
    INTERNAL: ("Внутренняя ошибка программы", "Отправить Claude", "claude"),
}

TRANSIENT = {NETWORK, RATE_LIMIT, FILE_BUSY}


class HubError(Exception):
    """Ошибка с категорией. message — понятный пользователю текст."""

    def __init__(self, category, message="", detail=""):
        super().__init__(message or CATEGORIES.get(category, ("",))[0])
        self.category = category
        self.detail = detail


class StructureError(HubError):
    def __init__(self, message, detail=""):
        super().__init__(STRUCTURE, message, detail)


class LoginRequired(HubError):
    def __init__(self, message="Сайт требует вход", detail=""):
        super().__init__(LOGIN, message, detail)


class FileBusy(HubError):
    def __init__(self, path, detail=""):
        super().__init__(FILE_BUSY, f"Файл занят (открыт в Excel?): {path}", detail)
        self.path = path


def classify(exc) -> str:
    if isinstance(exc, HubError):
        return exc.category
    if isinstance(exc, urllib.error.HTTPError):
        code = exc.code
    else:
        code = getattr(getattr(exc, "response", None), "status_code", None)
    if code in (429, 503):
        return RATE_LIMIT
    if code in (401,):
        return LOGIN
    if code == 403:
        return BOT
    if code and code >= 500:
        return NETWORK
    if code and code >= 400:
        return STRUCTURE
    if isinstance(exc, PermissionError):
        return FILE_BUSY
    if isinstance(exc, (urllib.error.URLError, socket.timeout, TimeoutError, ConnectionError, socket.gaierror)):
        return NETWORK
    name = type(exc).__name__
    if name in ("ConnectionError", "ConnectTimeout", "ReadTimeout", "Timeout", "SSLError", "ProxyError"):
        return NETWORK
    return INTERNAL


def describe(category) -> str:
    what, todo, _ = CATEGORIES.get(category, CATEGORIES[INTERNAL])
    return f"{what}. {todo}."


def human_message(exc) -> str:
    """Короткий понятный текст ошибки без технических слов."""
    cat = classify(exc)
    if isinstance(exc, HubError) and str(exc):
        return str(exc)
    what, _todo, _ = CATEGORIES.get(cat, CATEGORIES[INTERNAL])
    if cat == INTERNAL:
        return f"{what}: {type(exc).__name__}: {exc}"
    return what
