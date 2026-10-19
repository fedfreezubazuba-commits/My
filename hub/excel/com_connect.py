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


def _add_queries(wb, packet_path, log):
    for name, code in mcode.queries(packet_path):
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


def _load_table(ws, query_name, cell, log):
    for lo in ws.ListObjects:
        if lo.Name == query_name:
            log(f"Таблица {query_name} уже есть — обновляю")
            lo.QueryTable.Refresh(False)
            return lo
    log(f"Загружаю запрос {query_name} в таблицу на листе «Хаб» ({cell})")
    conn = (f"OLEDB;Provider=Microsoft.Mashup.OleDb.1;Data Source=$Workbook$;"
            f"Location={query_name};Extended Properties=\"\"")
    lo = ws.ListObjects.Add(SourceType=XL_SRC_EXTERNAL, Source=conn, Destination=ws.Range(cell))
    lo.Name = query_name
    qt = lo.QueryTable
    qt.CommandType = XL_CMD_SQL
    qt.CommandText = [f"SELECT * FROM [{query_name}]"]
    qt.BackgroundQuery = False
    qt.RefreshOnFileOpen = True
    qt.PreserveColumnInfo = True
    qt.AdjustColumnWidth = True
    qt.Refresh(False)
    try:
        qt.WorkbookConnection.Name = f"Запрос — {query_name}"
        qt.WorkbookConnection.OLEDBConnection.RefreshOnFileOpen = True
        qt.WorkbookConnection.OLEDBConnection.BackgroundQuery = False
    except Exception as e:  # noqa: BLE001
        log(f"Настройка подключения {query_name}: {e}")
    return lo


def _fill(wb, packet_path, log):
    _add_queries(wb, packet_path, log)
    ws = _sheet(wb, log)
    _load_table(ws, "Хаб", "A1", log)
    used = ws.ListObjects("Хаб").Range.Columns.Count
    # справочник ставим правее с запасом, чтобы новые ряды Хаба не упёрлись в него
    col = max(used + 3, 60)
    from_cell = ws.Cells(1, col).Address(False, False)
    _load_table(ws, "Хаб_Ряды", from_cell, log)
    return ws


def connect_model(model_path, packet_path, backups_dir, log=print) -> dict:
    """Подключить модель. Возвращает {'backup': путь копии, 'steps': [...]}."""
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
    try:
        say(f"Открываю модель {model_path.name}")
        wb = xl.Workbooks.Open(str(model_path), UpdateLinks=0, ReadOnly=False)
        _fill(wb, packet_path, say)
        say("Сохраняю модель")
        wb.Save()
        wb.Close(SaveChanges=False)
        wb = None
        say("Готово")
    except HubError:
        raise
    except Exception as e:  # noqa: BLE001
        raise HubError(INTERNAL, f"Excel не смог подключить модель: {e}. Модель не изменена — "
                                 f"резервная копия: {copy}", detail="\n".join(steps))
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
    return {"backup": str(copy), "steps": steps}


def make_connection_file(target: Path, packet_path, log=print) -> Path:
    """Запасной путь: отдельная книга «<модель> — подключение.xlsx» с листом «Хаб» и запросами."""
    target = Path(target)
    xl = _excel(log)
    wb = None
    try:
        wb = xl.Workbooks.Add()
        _fill(wb, packet_path, log)
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
