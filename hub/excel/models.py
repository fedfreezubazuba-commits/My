"""
Модели пользователя: подключение, отключение, «где новые данные».

Подключение идёт по ступеням — от самой удобной к самой надёжной:
  1. COM: хаб сам добавляет лист «Хаб» и запросы в модель (с резервной копией);
  2. если не вышло — файл «<модель> — подключение.xlsx» с готовыми запросами (тоже через Excel);
  3. если Excel недоступен совсем — текстовая инструкция с M-кодом запросов для вставки вручную.
"""
import datetime as dt
import os
from pathlib import Path

from ..core.errors import FileBusy, HubError, human_message
from . import formulas, mcode, packet


def manual_instructions(model_name, packet_path, layout="long") -> str:
    q = dict(mcode.queries(packet_path, layout=layout))
    return f"""Как подключить модель «{model_name}» к Хабу вручную
=====================================================

Хаб не смог сам добавить запросы в модель (например, Excel не запустился в фоне).
Это можно сделать руками за 5 минут — один раз.

1. Откройте модель в Excel.
2. Данные → Получить данные → Из других источников → Пустой запрос.
   Откроется редактор Power Query.
3. Слева в списке запросов: правой кнопкой по «Запрос1» → Переименовать → Хаб_Посылка.
   Главная → Расширенный редактор → удалите всё и вставьте текст из блока «Хаб_Посылка» ниже → Готово.
4. Повторите шаг 2–3 ещё два раза: запрос «Хаб» и запрос «Хаб_Ряды» (тексты ниже).
5. Главная → Закрыть и загрузить в… → Таблица → На новый лист — для запросов «Хаб» и «Хаб_Ряды».
   Переименуйте этот лист в «Хаб». Таблицы должны называться Хаб и Хаб_Ряды
   (Конструктор таблиц → Имя таблицы).
6. Данные → Запросы и подключения → правой кнопкой по «Хаб» → Свойства →
   включите «Обновлять при открытии файла». То же для «Хаб_Ряды».
6а. История обновлений (необязательно): ещё один пустой запрос «Хаб_Обновления» (текст ниже) →
   «Закрыть и загрузить в…» → Таблица → На новый лист, лист назовите «Хаб_Обновления». В ней — когда какие
   ряды обновлялись; удобно сравнивать с датой последнего пересчёта модели.
7. Если Excel спросит про уровни конфиденциальности — выберите «Игнорировать уровни
   конфиденциальности» (Файл → Параметры → Конфиденциальность для этой книги).
8. Сохраните модель. Дальше данные обновляются кнопкой «Данные → Обновить всё»
   и при каждом открытии файла.

Пример формулы: {formulas.main_formula("КОД_РЯДА", layout=layout)}

--- Хаб_Посылка -------------------------------------------------
{q["Хаб_Посылка"]}

--- Хаб ---------------------------------------------------------
{q["Хаб"]}

--- Хаб_Ряды ----------------------------------------------------
{q["Хаб_Ряды"]}

--- Хаб_Обновления ----------------------------------------------
{q["Хаб_Обновления"]}
"""


def connect(cfg, store, paths, name, model_path, log=print, use_com=True) -> dict:
    """Подключить модель. Возвращает {'ok', 'method', 'message', 'formula', 'files'}."""
    name = name.strip()
    model_path = Path(model_path)
    if not cfg.model(name):
        cfg.add_model(name, str(model_path))
    else:
        cfg.update_model(name, path=str(model_path), active=True)
    model = cfg.model(name)
    lay = packet.layout(cfg, model)
    packet.rebuild(cfg, store, paths, models=[model], log=None)
    ppath = packet.packet_path(paths, name)
    result = {"ok": False, "method": None, "files": [], "packet": str(ppath),
              "formula": formulas.main_formula("КОД_РЯДА", cfg.settings().get("formula_locale", "ru"), lay)}
    errors = []
    load_hint = ("Чтобы закончить, откройте модель: Данные → Запросы и подключения → правой кнопкой по «Хаб» → "
                 "«Загрузить в…» → Таблица → «Имеющийся лист» → лист «Хаб», ячейка A1 → ОК. То же для «Хаб_Ряды» "
                 "(ячейка BH1). Сохраните модель.")
    if use_com:
        try:
            prefer = cfg.settings().get("excel_way") or None
            r = com_connect().connect_model(model_path, ppath, paths.model_backups, log=log, prefer=prefer, layout=lay)
            if r.get("way"):
                cfg.update_settings(excel_way=r["way"])     # в следующий раз начнём с того, что сработало
            if r.get("partial"):
                result.update(ok=True, method="com_partial", backup=r["backup"],
                              message=f"Модель «{name}» почти подключена: запросы Хаб и Хаб_Ряды добавлены, но Excel "
                                      f"не дал хабу самому вывести их на лист. {load_hint}")
            else:
                result.update(ok=True, method="com", backup=r["backup"],
                              message=f"Модель «{name}» подключена: добавлены лист «Хаб» с таблицами Хаб и Хаб_Ряды и лист "
                                      "«Хаб_Обновления» — когда какие ряды обновлялись. "
                                      f"Резервная копия: {r['backup']}")
            cfg.update_model(name, connected_method=result["method"], connected=dt.date.today().isoformat(),
                             layout=lay)
            return result
        except FileBusy:
            raise
        except Exception as e:  # noqa: BLE001
            errors.append(human_message(e))
            log(f"Подключение через Excel не удалось: {e!r}\n{getattr(e, 'detail', '')}")
        try:
            target = paths.packets / f"{packet.safe_name(name)} — подключение.xlsx"
            com_connect().make_connection_file(target, ppath, log=log, layout=lay)
            result["files"].append(str(target))
            result.update(ok=True, method="file",
                          message=(f"Не удалось изменить модель автоматически ({errors[-1]}). Модель не изменена. "
                                   f"Создан файл «{target.name}» с готовыми запросами. Откройте его и модель: "
                                   f"Данные → Запросы и подключения → выделите три запроса → Копировать → в модели "
                                   f"вставьте их в ту же панель. Подробно — в инструкции рядом."))
        except Exception as e:  # noqa: BLE001
            errors.append(human_message(e))
            log(f"Файл-подключение не создан: {e!r}\n{getattr(e, 'detail', '')}")
    instr = paths.packets / f"{packet.safe_name(name)} — как подключить вручную.txt"
    instr.write_text(manual_instructions(name, ppath, lay), encoding="utf-8-sig")
    result["files"].append(str(instr))
    if not result["ok"]:
        reason = errors[0] if errors else "это не Windows"
        result.update(method="manual",
                      message=(f"Автоматически подключить не получилось: {reason}. Модель не изменена.\n\n"
                               f"Данные для модели уже готовы. Подключите их вручную по инструкции "
                               f"«{instr.name}» (один раз, около 5 минут) — или нажмите «Проверить связь с Excel» "
                               f"и отправьте результат Claude, чтобы он поправил автоматическое подключение."))
    cfg.update_model(name, connected_method=result["method"])
    return result


def com_connect():
    from . import com_connect as cc
    return cc


def disconnect(cfg, name):
    """Хаб перестаёт обновлять посылку; саму модель не трогаем."""
    cfg.update_model(name, active=False, disconnected=dt.date.today().isoformat())


def models_with_new_data(cfg, store) -> list[tuple[str, int]]:
    """Модели, в чьих посылках есть данные новее последнего сохранения модели."""
    out = []
    for m in cfg.models():
        if not m.get("active", True):
            continue
        st = store.model_state(m["name"])
        changed = st.get("packet_changed_at")
        if not changed:
            continue
        try:
            saved = dt.datetime.fromtimestamp(os.path.getmtime(m["path"])).isoformat(timespec="seconds")
        except (OSError, TypeError):
            saved = ""
        if changed > saved:
            codes = store.subscribed(m["name"])
            n = len(store.changed_since(codes, saved)) if saved else len(codes)
            if n:
                out.append((m["name"], n))
    return out


def connect_error_text(e) -> str:
    if isinstance(e, FileBusy):
        return "Модель открыта в Excel. Закройте её и нажмите «Подключить модель» ещё раз."
    if isinstance(e, HubError):
        return str(e)
    return human_message(e)
