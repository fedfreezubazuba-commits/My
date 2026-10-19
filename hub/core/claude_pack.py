"""
«Отправить Claude» (раздел 12 ТЗ): пакет для новой сессии Claude Code — описание показателя,
параметры, ошибка с трассировкой, кусок сырого ответа, версия приложения.
Пароли, куки и токены в пакет не попадают (вычищаются).

Тот же механизм формирует «запрос на новый источник» с вкладки «Сайт».
"""
import datetime as dt
import json
import platform
import re
from pathlib import Path

from .. import VERSION
from . import errors as E

# имена полей с секретами (поле «key» у показателя — обычное имя, его не трогаем)
SECRET_KEYS = re.compile(r"^(pass(word)?|passwd|pwd|.*token|.*secret|session(id)?|.*cookies?|auth(orization)?|"
                         r"api[_-]?key|access[_-]?key|private[_-]?key|csrf.*|sid|jwt|bearer)$", re.I)
SECRET_PATTERNS = [
    (re.compile(r'(name="(?:struts\.token\.name|token|csrf[^"]*|_token)"[^>]*value=")[^"]*(")', re.I), r"\1***\2"),
    (re.compile(r'((?:token|csrf|session|sid|password|passwd|pwd|secret|api[_-]?key)["\']?\s*[:=]\s*["\']?)[^"\'&\s,;}{]+',
                re.I), r"\1***"),
    (re.compile(r"(Authorization:\s*)(\S+\s*)?\S+", re.I), r"\1***"),
    (re.compile(r"(Cookie:\s*).*", re.I), r"\1***"),
    (re.compile(r"(Set-Cookie:\s*).*", re.I), r"\1***"),
]


def scrub(text: str) -> str:
    if not text:
        return ""
    for pat, rep in SECRET_PATTERNS:
        text = pat.sub(rep, text)
    return text


def scrub_obj(obj):
    if isinstance(obj, dict):
        return {k: ("***" if SECRET_KEYS.search(str(k)) else scrub_obj(v)) for k, v in obj.items()}
    if isinstance(obj, list):
        return [scrub_obj(v) for v in obj]
    if isinstance(obj, str):
        return scrub(obj)
    return obj


def raw_excerpt(path, limit=6000) -> str:
    if not path:
        return ""
    try:
        data = Path(path).read_bytes()[:limit * 2]
    except OSError:
        return ""
    for enc in ("utf-8", "cp1251"):
        try:
            return scrub(data.decode(enc)[:limit])
        except UnicodeDecodeError:
            continue
    return scrub(data.decode("utf-8", "replace")[:limit])


def job_package(cfg, store, job_id) -> str:
    job = cfg.job(job_id) or next((j for j in cfg.trash() if j["id"] == job_id), {"id": job_id})
    st = store.state(job_id)
    runs = store.runs(job_id, 5)
    last_err = next((r for r in runs if r["status"] in ("error", "retry", "login", "quarantine")), None)
    raw_path = next((r["raw_path"] for r in runs if r["raw_path"]), None)
    series = store.series_of_job(job_id)
    cat = (last_err["category"] if last_err else st.get("category")) or E.INTERNAL
    lines = [
        "# Запрос на правку источника «Хаба данных»",
        "",
        f"Дата: {dt.datetime.now():%Y-%m-%d %H:%M}. Версия приложения: {VERSION}. "
        f"Система: {platform.system()} {platform.release()}, Python {platform.python_version()}.",
        "",
        "Прочитай CLAUDE.md и SPEC.md в репозитории. Ниже — показатель, который перестал загружаться.",
        "Нужно найти причину, поправить адаптер источника и добавить тест на образце из samples/.",
        "",
        "## Показатель",
        f"- id: `{job.get('id')}`",
        f"- название: {job.get('name', '')}",
        f"- источник: `{job.get('source', '')}`",
        f"- расписание: {job.get('schedule', '')}",
        "- параметры:",
        "```json",
        json.dumps(scrub_obj(job.get("params", {})), ensure_ascii=False, indent=2),
        "```",
        "",
        "## Что случилось",
        f"- статус: {st.get('status')}, категория ошибки: `{cat}` — {E.describe(cat)}",
        f"- сообщение: {scrub(st.get('message') or '')}",
        f"- последняя удачная загрузка: {st.get('last_success') or 'не было'}",
        f"- сбоев подряд: {st.get('fail_count') or 0}",
    ]
    if last_err is not None and last_err["traceback"]:
        lines += ["", "## Трассировка", "```", scrub(last_err["traceback"])[-6000:], "```"]
    if series:
        lines += ["", "## Ряды показателя (коды не менять!)"]
        lines += [f"- `{s['code']}` — {s['name']} [{s['unit']}], {s['periodicity']}" for s in series[:30]]
    excerpt = raw_excerpt(raw_path)
    if excerpt:
        lines += ["", f"## Начало последнего сырого ответа ({Path(raw_path).name})", "```", excerpt, "```"]
    lines += ["", "Пароли, куки и токены из пакета удалены."]
    return "\n".join(lines)


def new_source_package(url, want, page_path=None, notes="") -> str:
    lines = [
        "# Запрос на новый источник для «Хаба данных»",
        "",
        f"Дата: {dt.datetime.now():%Y-%m-%d %H:%M}. Версия приложения: {VERSION}.",
        "",
        "Прочитай CLAUDE.md и КАК_ДОБАВИТЬ_ИСТОЧНИК.md. Мастер «Сайт» не справился с этой страницей.",
        "Нужно добавить источник (или профиль сайта) и тест на сохранённой странице.",
        "",
        f"- Ссылка: {scrub(url)}",
        f"- Что нужно извлечь: {want}",
    ]
    if notes:
        lines.append(f"- Что уже пробовали: {notes}")
    if page_path:
        lines.append(f"- Сохранённая страница: {page_path} (положите её в samples/web/ репозитория)")
    lines += ["", "Пароли, куки и токены в пакет не включены."]
    return "\n".join(lines)


def save(paths, text, name) -> Path:
    paths.claude.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^\w\-]+", "_", name)[:60]
    p = paths.claude / f"{dt.datetime.now():%Y-%m-%d_%H%M}_{safe}.md"
    p.write_text(text, encoding="utf-8")
    return p
