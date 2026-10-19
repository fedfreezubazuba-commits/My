"""
Подключение модели через COM-автоматизацию Excel (раздел 9.2 ТЗ). Работает только в Windows
с установленным Excel и пакетом pywin32. На других системах функции поднимают понятную ошибку.

Что делает «Подключить модель»:
  1. проверяет, что файл модели закрыт (иначе просит закрыть);
  2. делает резервную копию в backups/модели/;
  3. в невидимом Excel добавляет запросы Хаб_Посылка, Хаб, Хаб_Ряды
     (Workbook.Queries.Add), лист «Хаб» с таблицами Хаб и Хаб_Ряды, включает
     «Обновлять при открытии файла» и отключает вопросы об уровнях конфиденциальности
     для этой книги (Queries.FastCombine), сохраняет и закрывает. Остальное в модели не трогается.

Каждый шаг пишется в журнал — так при сбое у пользователя Claude сможет понять, где именно.
"""
import datetime as dt
import shutil
import time
from pathlib import Path

from ..core.errors import FileBusy, HubError, INTERNAL
from . import mcode

SHEET = "Хаб"
XL_SRC_EXTERNAL = 0
XL_CMD_SQL = 2
XL_YES = 1
XL_OPENXML_WORKBOOK = 51
XL_OPENXML_WORKBOOK_MACRO = 52


class ExcelUnavailable(HubError):
    def __init__(self, msg):
        super().__init__(INTERNAL, msg)


def is_file_open(path: Path) -> bool:
    """Открыт ли файл в Excel. Excel держит рядом файл-замок «~$имя» и не даёт переименовать файл."""
    path = Path(path)
    # Excel создаёт «~$Имя.xlsx», Word-подобные программы — «~$» вместо первых двух букв
    locks = [path.with_name("~$" + path.name), path.with_name("~$" + path.name[2:])]
    if any(lock.exists() for lock in locks):
        return True
    try:
        tmp = path.with_name(path.name + ".hubcheck")
        path.rename(tmp)
        tmp.rename(path)
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def backup(path: Path, backups_dir: Path) -> Path:
    backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y-%m-%d_%H%M%S")
    dst = backups_dir / f"{path.stem} {stamp}{path.suffix}"
    shutil.copy2(path, dst)
    return dst


def _excel(log):
    try:
        import pythoncom
        import win32com.client as win32
    except ImportError:
        raise ExcelUnavailable("Не найден модуль pywin32 — подключение через Excel недоступно. "
                               "Запустите «Установка.bat» ещё раз.")
    try:
        pythoncom.CoInitialize()
    except Exception:  # noqa: BLE001
        pass
    log("Запускаю Excel в фоне")
    try:
        xl = win32.DispatchEx("Excel.Application")
    except Exception as e:  # noqa: BLE001
        raise ExcelUnavailable(f"Не удалось запустить Excel: {e}")
    xl.Visible = False
    xl.DisplayAlerts = False
    xl.ScreenUpdating = False
    return xl


def _add_queries(wb, packet_path, log, ascii_names=False):
    """Добавить (или обновить) три запроса хаба. Запросы другого набора имён удаляются."""
    other = set(mcode.query_names(not ascii_names).values()) - set(mcode.query_names(ascii_names).values())
    for q in list(wb.Queries):
        if q.Name in other:
            log(f"Удаляю запрос {q.Name} (другой набор имён)")
            q.Delete()
    for name, code in mcode.queries(packet_path, ascii_names):
        existing = None
        for q in wb.Queries:
            if q.Name == name:
                existing = q
        if existing is not None:
            log(f"Запрос {name} уже есть — обновляю формулу")
            existing.Formula = code
        else:
            log(f"Добавляю запрос {name}")
            wb.Queries.Add(name, code)
    try:
        wb.Queries.FastCombine = True
        log("Вопросы об уровнях конфиденциальности для этой книги отключены (FastCombine)")
    except Exception as e:  # noqa: BLE001
        log(f"Не удалось отключить вопросы о конфиденциальности: {e}")


def _sheet(wb, log):
    for ws in wb.Worksheets:
        if ws.Name == SHEET:
            log("Лист «Хаб» уже есть")
            return ws
    log("Создаю лист «Хаб» в конце книги")
    ws = wb.Worksheets.Add(After=wb.Worksheets(wb.Worksheets.Count))
    ws.Name = SHEET
    return ws


def _conn_string(query_name, quoted=False):
    loc = f'"{query_name}"' if quoted else query_name
    return (f"OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;"
            f"Location={loc};Extended Properties=\"\"")


def _missing():
    try:
        import pythoncom
        return pythoncom.Missing
    except ImportError:
        return None


# Способы загрузить запрос в таблицу листа. Разные версии Excel и pywin32 по-разному принимают
# аргументы ListObjects.Add, поэтому пробуем по очереди и запоминаем, какой сработал.
def _way_positional(ws, query_name, cell):
    return ws.ListObjects.Add(XL_SRC_EXTERNAL, _conn_string(query_name), _missing(), XL_YES, ws.Range(cell))


def _way_keywords(ws, query_name, cell):
    return ws.ListObjects.Add(SourceType=XL_SRC_EXTERNAL, Source=_conn_string(query_name),
                              Destination=ws.Range(cell))


def _way_array(ws, query_name, cell):
    return ws.ListObjects.Add(SourceType=XL_SRC_EXTERNAL, Source=[_conn_string(query_name)],
                              Destination=ws.Range(cell))


def _way_quoted(ws, query_name, cell):
    return ws.ListObjects.Add(SourceType=XL_SRC_EXTERNAL, Source=_conn_string(query_name, quoted=True),
                              Destination=ws.Range(cell))


def _way_connection(ws, query_name, cell):
    wb = ws.Parent
    name = f"Запрос — {query_name}"
    conn = None
    for c in wb.Connections:
        if c.Name == name:
            conn = c
    if conn is None:
        conn = wb.Connections.Add2(name, f"Хаб данных: {query_name}", _conn_string(query_name),
                                   f"SELECT * FROM [{query_name}]", XL_CMD_SQL)
    return ws.ListObjects.Add(XL_SRC_EXTERNAL, conn, _missing(), XL_YES, ws.Range(cell))


# Журнал пользователя: QueryTables.Add (все 3 аргумента заданы) работает, а ListObjects.Add — нет.
# Вероятная причина: pywin32 подставляет «пустое значение» (VT_EMPTY) вместо пропущенных необязательных
# аргументов (LinkSource, TableStyleName), и Excel отвечает «неверный аргумент». Поэтому сначала —
# все аргументы явно, затем — прямой вызов COM ровно с теми аргументами, что переданы.
TABLE_STYLE = "TableStyleMedium2"


def _way_all_args(ws, query_name, cell):
    return ws.ListObjects.Add(XL_SRC_EXTERNAL, _conn_string(query_name), True, XL_YES, ws.Range(cell),
                              TABLE_STYLE)


def _way_all_named(ws, query_name, cell):
    return ws.ListObjects.Add(SourceType=XL_SRC_EXTERNAL, Source=_conn_string(query_name), LinkSource=True,
                              XlListObjectHasHeaders=XL_YES, Destination=ws.Range(cell),
                              TableStyleName=TABLE_STYLE)


def _way_raw_invoke(ws, query_name, cell):
    import pythoncom
    import win32com.client
    ole = ws.ListObjects._oleobj_
    dispid = ole.GetIDsOfNames("Add")
    res = ole.Invoke(dispid, 0, pythoncom.DISPATCH_METHOD, True,
                     XL_SRC_EXTERNAL, _conn_string(query_name), True, XL_YES, ws.Range(cell)._oleobj_)
    return win32com.client.Dispatch(res)


TABLE_WAYS = [("ListObjects.Add (все аргументы явно)", _way_all_args),
              ("ListObjects.Add (все аргументы по именам)", _way_all_named),
              ("ListObjects.Add (прямой вызов COM)", _way_raw_invoke),
              ("ListObjects.Add (по порядку)", _way_positional),
              ("ListObjects.Add (по именам)", _way_keywords),
              ("ListObjects.Add (подключение списком)", _way_array),
              ("ListObjects.Add (имя в кавычках)", _way_quoted),
              ("Connections.Add2 + ListObjects.Add", _way_connection)]
WAYS = dict(TABLE_WAYS)

# варианты окружения: (Excel виден, латинские имена запросов)
VARIANTS = [(False, False), (False, True), (True, False), (True, True)]
XL_MINIMIZED = -4140


def variant_title(visible, ascii_names):
    return (("видимый Excel" if visible else "Excel в фоне") + ", " +
            ("латинские имена запросов" if ascii_names else "русские имена запросов"))


def _set_visible(wb, visible, log):
    try:
        xl = wb.Application
        xl.Visible = bool(visible)
        if visible:
            xl.WindowState = XL_MINIMIZED
            try:
                wb.Activate()          # как при записи макроса: книга активна
            except Exception:  # noqa: BLE001
                pass
    except Exception as e:  # noqa: BLE001
        log(f"  не удалось переключить видимость Excel: {e!r}")


class StepError(HubError):
    """Ошибка на конкретном шаге подключения — с понятным названием шага."""

    def __init__(self, step, exc):
        super().__init__(INTERNAL, f"Excel не смог выполнить шаг «{step}»", detail=f"{step}: {exc!r}")
        self.step = step
        self.exc = exc


def _finish_table(lo, query_name, table_name, log):
    qt = lo.QueryTable
    for prop, value in (("CommandType", XL_CMD_SQL), ("CommandText", [f"SELECT * FROM [{query_name}]"]),
                        ("BackgroundQuery", False), ("RefreshOnFileOpen", True), ("PreserveColumnInfo", True),
                        ("AdjustColumnWidth", True)):
        try:
            setattr(qt, prop, value)
        except Exception as e:  # noqa: BLE001
            log(f"  свойство {prop}: {e!r}")
    try:
        lo.Name = table_name
    except Exception as e:  # noqa: BLE001
        log(f"  имя таблицы {table_name}: {e!r}")
    try:
        qt.Refresh(False)
    except Exception as e:  # noqa: BLE001
        raise StepError(f"обновить таблицу {table_name} (Power Query)", e)
    try:
        qt.WorkbookConnection.Name = f"Запрос — {query_name}"
        qt.WorkbookConnection.OLEDBConnection.RefreshOnFileOpen = True
        qt.WorkbookConnection.OLEDBConnection.BackgroundQuery = False
    except Exception as e:  # noqa: BLE001
        log(f"  настройка подключения {table_name}: {e!r}")
    return lo


def _existing_table(ws, table_name):
    for lo in ws.ListObjects:
        if lo.Name == table_name:
            return lo
    return None


def _try_ways(ws, query_name, cell, log, ways):
    errors = []
    for title in ways:
        try:
            lo = WAYS[title](ws, query_name, cell)
            log(f"  способ «{title}» сработал")
            return lo, title, errors
        except Exception as e:  # noqa: BLE001
            errors.append(f"{title}: {e!r}")
            log(f"  способ «{title}» не сработал: {e!r}")
    return None, None, errors


def _load_tables(wb, ws, packet_path, log, prefer=None):
    """Вывести запросы Хаб и Хаб_Ряды таблицами на лист. Перебирает окружения и способы;
    возвращает {'visible', 'ascii', 'way'} того, что сработало (чтобы в следующий раз начать с него)."""
    hub_lo = _existing_table(ws, "Хаб")
    if hub_lo is not None and _existing_table(ws, "Хаб_Ряды") is not None:
        log("Таблицы Хаб и Хаб_Ряды уже есть — обновляю")
        for name in ("Хаб", "Хаб_Ряды"):
            try:
                _existing_table(ws, name).QueryTable.Refresh(False)
            except Exception as e:  # noqa: BLE001
                raise StepError(f"обновить таблицу {name} (Power Query)", e)
        return dict(prefer or {})
    variants = list(VARIANTS)
    ways = [t for t, _ in TABLE_WAYS]
    if prefer:
        pv = (bool(prefer.get("visible")), bool(prefer.get("ascii")))
        if pv in variants:
            variants.remove(pv)
            variants.insert(0, pv)
        if prefer.get("way") in ways:
            ways.remove(prefer["way"])
            ways.insert(0, prefer["way"])
    all_errors = []
    try:
        for visible, ascii_names in variants:
            log(f"Пробую: {variant_title(visible, ascii_names)}")
            _set_visible(wb, visible, log)
            _add_queries(wb, packet_path, log, ascii_names)
            names = mcode.query_names(ascii_names)
            lo, way, errors = _try_ways(ws, names["Хаб"], "A1", log, ways)
            all_errors += errors
            if lo is None:
                continue
            _finish_table(lo, names["Хаб"], "Хаб", log)
            try:
                used = lo.Range.Columns.Count
            except Exception:  # noqa: BLE001
                used = 10
            cell = f"{col_letter(max(used + 3, 60))}1"
            lo2, _w, errors = _try_ways(ws, names["Хаб_Ряды"], cell, log, [way] + [w for w in ways if w != way])
            if lo2 is None:
                raise StepError("создать таблицу Хаб_Ряды", "; ".join(errors))
            _finish_table(lo2, names["Хаб_Ряды"], "Хаб_Ряды", log)
            return {"visible": visible, "ascii": ascii_names, "way": way}
    finally:
        _set_visible(wb, False, log)
    # ничего не сработало: оставляем запросы с русскими именами — по ним написана инструкция
    _add_queries(wb, packet_path, log, False)
    raise StepError("создать таблицу Хаб", "; ".join(all_errors[-5:]))


def col_letter(n: int) -> str:
    s = ""
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s


def _step(name, fn, *args, log):
    log(name)
    try:
        return fn(*args)
    except HubError:
        raise
    except Exception as e:  # noqa: BLE001
        raise StepError(name, e)


def _fill(wb, packet_path, log, prefer=None):
    _step("Добавляю запросы Power Query", _add_queries, wb, packet_path, log, log=log)
    ws = _step("Готовлю лист «Хаб»", _sheet, wb, log, log=log)
    return ws, _load_tables(wb, ws, packet_path, log, prefer)


def connect_model(model_path, packet_path, backups_dir, log=print, prefer=None) -> dict:
    """Подключить модель. Возвращает {'backup', 'steps', 'partial', 'way'}.

    partial=True — запросы добавлены и модель сохранена, но таблицы на лист загрузить не удалось:
    пользователю остаётся «Загрузить в…» (2 действия, инструкция в сообщении).
    way — какой способ сработал (сохраняется в настройках, чтобы в следующий раз начать с него)."""
    model_path = Path(model_path)
    steps = []

    def say(msg):
        steps.append(msg)
        log(msg)

    if not model_path.exists():
        raise HubError(INTERNAL, f"Файл модели не найден: {model_path}")
    if model_path.suffix.lower() not in (".xlsx", ".xlsm", ".xlsb"):
        raise HubError(INTERNAL, "Подключить можно книгу Excel .xlsx, .xlsm или .xlsb")
    if is_file_open(model_path):
        raise FileBusy(model_path, "Закройте модель в Excel и нажмите «Подключить модель» ещё раз.")
    copy = backup(model_path, backups_dir)
    say(f"Резервная копия: {copy}")
    xl = _excel(say)
    wb = None
    partial, way = False, None
    try:
        wb = _step(f"Открываю модель {model_path.name}", lambda: xl.Workbooks.Open(str(model_path), 0, False), log=say)
        try:
            _ws, way = _fill(wb, packet_path, say, prefer)
        except StepError as e:
            if not e.step.startswith(("создать таблицу", "обновить таблицу")):
                raise
            partial = True
            say(f"Таблицы не загрузились ({e.detail}). Сохраняю модель с запросами — осталось загрузить их вручную")
        _step("Сохраняю модель", wb.Save, log=say)
        wb.Close(False)
        wb = None
        say("Готово" if not partial else "Готово частично")
    except StepError as e:
        say(f"Ошибка: {e.detail}")
        raise HubError(INTERNAL, f"{e}. Модель не изменена — резервная копия: {copy}",
                       detail="\n".join(steps))
    except HubError:
        raise
    except Exception as e:  # noqa: BLE001
        say(f"Ошибка: {e!r}")
        raise HubError(INTERNAL, f"Excel не смог подключить модель. Модель не изменена — резервная копия: {copy}",
                       detail="\n".join(steps))
    finally:
        if wb is not None:
            try:
                wb.Close(False)
            except Exception:  # noqa: BLE001
                pass
        try:
            xl.Quit()
        except Exception:  # noqa: BLE001
            pass
    return {"backup": str(copy), "steps": steps, "partial": partial, "way": way}


def diagnose(packet_path, log=print) -> list[str]:
    """«Проверить связь с Excel»: все варианты подключения на новой пустой книге (модель не трогается).
    Возвращает подробный журнал — его можно отправить Claude. Последняя строка — итог."""
    out = []

    def say(msg):
        out.append(msg)
        log(msg)

    try:
        import sys
        import win32com  # noqa: F401 — проверяем, что pywin32 установлен
        try:
            from importlib.metadata import version
            pw = version("pywin32")
        except Exception:  # noqa: BLE001
            pw = "?"
        say(f"Python {sys.version.split()[0]}, pywin32 {pw}")
    except ImportError:
        say("pywin32 не установлен")
        return out
    try:
        xl = _excel(say)
    except HubError as e:
        say(str(e))
        return out
    wb = None
    worked = []
    try:
        say(f"Excel {xl.Version}, сборка {getattr(xl, 'Build', '?')}")
        wb = xl.Workbooks.Add()
        ws = wb.Worksheets(1)
        col = 1
        for visible, ascii_names in VARIANTS:
            say(f"--- {variant_title(visible, ascii_names)}")
            _set_visible(wb, visible, say)
            try:
                _add_queries(wb, packet_path, lambda m: None, ascii_names)
                names = mcode.query_names(ascii_names)
                say("Запросы добавлены: " + ", ".join(q.Name for q in wb.Queries))
            except Exception as e:  # noqa: BLE001
                say(f"Не удалось добавить запросы: {e!r}")
                continue
            for title, way in TABLE_WAYS:
                cell = f"{col_letter(col)}1"
                col += 6
                try:
                    lo = way(ws, names["Хаб"], cell)
                    try:
                        lo.QueryTable.Refresh(False)
                        say(f"Способ «{title}»: РАБОТАЕТ (строк {lo.ListRows.Count}, столбцов {lo.ListColumns.Count})")
                        worked.append(f"{variant_title(visible, ascii_names)} / {title}")
                    except Exception as e:  # noqa: BLE001
                        say(f"Способ «{title}»: таблица создана, но обновление не удалось: {e!r}")
                    try:
                        lo.Delete()
                    except Exception:  # noqa: BLE001
                        pass
                except Exception as e:  # noqa: BLE001
                    say(f"Способ «{title}»: ошибка {e!r}")
            # старый способ без «умной таблицы» — показывает, работает ли подключение к запросу вообще
            cell = f"{col_letter(col)}1"
            col += 6
            try:
                qt = ws.QueryTables.Add(_conn_string(names["Хаб"]), ws.Range(cell), f"SELECT * FROM [{names['Хаб']}]")
                qt.Refresh(False)
                say("Проверка QueryTables.Add (без таблицы): подключение к запросу работает")
                try:
                    qt.Delete()
                except Exception:  # noqa: BLE001
                    pass
            except Exception as e:  # noqa: BLE001
                say(f"Проверка QueryTables.Add (без таблицы): ошибка {e!r}")
    finally:
        if wb is not None:
            try:
                wb.Close(False)
            except Exception:  # noqa: BLE001
                pass
        try:
            xl.Quit()
        except Exception:  # noqa: BLE001
            pass
    say("ИТОГ: " + ("работает — " + "; ".join(worked) if worked else "ни один способ не сработал"))
    return out


def make_connection_file(target: Path, packet_path, log=print) -> Path:
    """Запасной путь: отдельная книга «<модель> — подключение.xlsx» с листом «Хаб» и запросами."""
    target = Path(target)
    xl = _excel(log)
    wb = None
    try:
        wb = xl.Workbooks.Add()
        try:
            _fill(wb, packet_path, log)
        except StepError as e:
            if not e.step.startswith(("создать таблицу", "обновить таблицу")):
                raise
            log(f"Таблицы не загрузились ({e.detail}); файл-подключение будет только с запросами")
        for ws in list(wb.Worksheets):
            if ws.Name != SHEET and wb.Worksheets.Count > 1:
                ws.Delete()
        if target.exists():
            target.unlink()
        wb.SaveAs(str(target), FileFormat=XL_OPENXML_WORKBOOK)
        wb.Close(SaveChanges=False)
        wb = None
        log(f"Создан файл-подключение {target}")
    finally:
        if wb is not None:
            try:
                wb.Close(SaveChanges=False)
            except Exception:  # noqa: BLE001
                pass
        try:
            xl.Quit()
        except Exception:  # noqa: BLE001
            pass
    return target


def refresh_closed_model(model_path, backups_dir, log=print) -> None:
    """Ночное обновление закрытой модели: открыть → «Обновить всё» → сохранить (с копией)."""
    model_path = Path(model_path)
    if is_file_open(model_path):
        raise FileBusy(model_path)
    backup(model_path, backups_dir)
    xl = _excel(log)
    wb = None
    try:
        wb = xl.Workbooks.Open(str(model_path), UpdateLinks=0)
        wb.RefreshAll()
        xl.CalculateUntilAsyncQueriesDone()
        time.sleep(1)
        wb.Save()
        wb.Close(SaveChanges=False)
        wb = None
    finally:
        if wb is not None:
            try:
                wb.Close(SaveChanges=False)
            except Exception:  # noqa: BLE001
                pass
        try:
            xl.Quit()
        except Exception:  # noqa: BLE001
            pass
