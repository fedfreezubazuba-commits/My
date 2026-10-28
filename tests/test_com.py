"""Подключение модели через COM — на поддельном Excel (настоящего в облаке нет).

Проверяем ход шагов: запасные способы создания таблицы, «частичное» подключение,
понятное название шага в ошибке и то, что модель без успеха не сохраняется."""
import sys
import types

import pytest


class COMError(Exception):
    pass


class FakeQueries(list):
    FastCombine = False

    def Add(self, name, formula):
        q = types.SimpleNamespace(Name=name, Formula=formula)
        q.Delete = lambda: self.remove(q)
        self.append(q)
        return q


class FakeQT:
    def __init__(self, ws):
        self.ws = ws
        self.WorkbookConnection = types.SimpleNamespace(Name="", OLEDBConnection=types.SimpleNamespace())

    def Refresh(self, background):
        if self.ws.wb.xl.fail_refresh:
            raise COMError("refresh")


class FakeLO:
    def __init__(self, ws):
        self.Name = "Таблица1"
        self.QueryTable = FakeQT(ws)
        self.Range = types.SimpleNamespace(Columns=types.SimpleNamespace(Count=3))


class FakeListObjects(list):
    def __init__(self, ws):
        super().__init__()
        self.ws = ws

    def __call__(self, name):
        return next(lo for lo in self if lo.Name == name)

    def Add(self, *args, **kw):
        xl = self.ws.wb.xl
        mode = xl.table_mode
        source = str(kw.get("Source", args[1] if len(args) > 1 else ""))
        bad = (mode == "never" or (mode == "keywords_only" and args)
               or (mode == "ascii_only" and "Location=Hub" not in source)
               or (mode == "visible_only" and not xl.Visible)
               or (mode == "explicit_only" and len(args) < 6 and "TableStyleName" not in kw))
        if bad:
            raise COMError((-2147352567, "Ошибка.", (0, None, None, None, 0, -2147024809), None))
        lo = FakeLO(self.ws)
        self.append(lo)
        return lo


class FakeWS:
    def __init__(self, wb, name):
        self.wb = wb
        self.Name = name
        self.ListObjects = FakeListObjects(self)
        self.Parent = wb

    def Range(self, cell):
        return cell


class FakeSheets(list):
    @property
    def Count(self):
        return len(self)

    def __call__(self, i):
        return self[i - 1]

    def Add(self, After=None):
        ws = FakeWS(self.wb, f"Лист{len(self) + 1}")
        self.append(ws)
        return ws


class FakeWB:
    def __init__(self, xl):
        self.xl = xl
        self.Application = xl
        self.Queries = FakeQueries()
        self.Worksheets = FakeSheets()
        self.Worksheets.wb = self
        self.Worksheets.append(FakeWS(self, "Модель"))
        self.Connections = []
        self.saved = False

    def Save(self):
        self.saved = True

    def Close(self, save=False):
        pass


class FakeXL:
    def __init__(self, table_mode="ok", fail_refresh=False):
        self.table_mode = table_mode
        self.fail_refresh = fail_refresh
        self.books = []
        self.Version = "16.0"
        self.Visible = False
        self.WindowState = 0
        self.Workbooks = types.SimpleNamespace(Open=self._open, Add=self._open)

    def _open(self, *a, **k):
        wb = FakeWB(self)
        self.books.append(wb)
        return wb

    def Quit(self):
        pass


@pytest.fixture
def fake_excel(monkeypatch):
    holder = {}
    pythoncom = types.ModuleType("pythoncom")
    pythoncom.CoInitialize = lambda: None
    pythoncom.Missing = object()
    client = types.ModuleType("win32com.client")
    client.DispatchEx = lambda name: holder["xl"]
    win32com = types.ModuleType("win32com")
    win32com.client = client
    monkeypatch.setitem(sys.modules, "pythoncom", pythoncom)
    monkeypatch.setitem(sys.modules, "win32com", win32com)
    monkeypatch.setitem(sys.modules, "win32com.client", client)
    return holder


def model_file(tmp_path):
    p = tmp_path / "Модель.xlsx"
    p.write_bytes(b"PK fake")
    return p


@pytest.mark.parametrize("mode", ["ok", "keywords_only"])
def test_connect_uses_fallback_ways(fake_excel, tmp_path, mode):
    from hub.excel import com_connect
    fake_excel["xl"] = FakeXL(mode)
    r = com_connect.connect_model(model_file(tmp_path), tmp_path / "М.csv", tmp_path / "bk", log=lambda m: None)
    wb = fake_excel["xl"].books[0]
    assert not r["partial"] and wb.saved
    assert [q.Name for q in wb.Queries] == ["Хаб_Посылка", "Хаб", "Хаб_Ряды", "Хаб_Обновления"]
    hub = next(ws for ws in wb.Worksheets if ws.Name == "Хаб")
    assert [lo.Name for lo in hub.ListObjects] == ["Хаб", "Хаб_Ряды"]
    assert wb.Queries.FastCombine is True
    assert (tmp_path / "bk").exists()


def test_connect_partial_when_tables_impossible(fake_excel, tmp_path):
    from hub.excel import com_connect
    fake_excel["xl"] = FakeXL("never")
    steps = []
    r = com_connect.connect_model(model_file(tmp_path), tmp_path / "М.csv", tmp_path / "bk", log=steps.append)
    assert r["partial"] is True
    wb = fake_excel["xl"].books[0]
    assert wb.saved and len(wb.Queries) == 4        # запросы сохранены — осталось «Загрузить в…»
    assert any("не сработал" in s for s in steps)


def test_connect_error_names_step_and_keeps_model(fake_excel, tmp_path, monkeypatch):
    from hub.core.errors import HubError
    from hub.excel import com_connect
    fake_excel["xl"] = FakeXL()

    def broken(*a):
        raise COMError("bad arg")
    monkeypatch.setattr(FakeQueries, "Add", broken)
    with pytest.raises(HubError) as e:
        com_connect.connect_model(model_file(tmp_path), tmp_path / "М.csv", tmp_path / "bk", log=lambda m: None)
    assert "Добавляю запросы Power Query" in str(e.value) and "не изменена" in str(e.value)
    assert not fake_excel["xl"].books[0].saved


def test_models_connect_partial_message(fake_excel, workdir):
    from hub.core.config import Config
    from hub.core.paths import Paths
    from hub.core.store import Store
    from hub.excel import models
    fake_excel["xl"] = FakeXL("never")
    paths = Paths(workdir).ensure()
    cfg, store = Config(paths), Store(paths.db)
    f = workdir / "Модель.xlsx"
    f.write_bytes(b"PK")
    try:
        r = models.connect(cfg, store, paths, "Тест", f, log=lambda m: None)
    finally:
        store.close()
    assert r["ok"] and r["method"] == "com_partial" and "Загрузить в" in r["message"]


def test_diagnose_reports_each_way(fake_excel, tmp_path):
    from hub.excel import com_connect
    fake_excel["xl"] = FakeXL("keywords_only")
    lines = com_connect.diagnose(tmp_path / "т.csv", log=lambda m: None)
    text = "\n".join(lines)
    assert "(по порядку)»: ошибка" in text and "(по именам)»: РАБОТАЕТ" not in text  # ListRows нет у подделки
    assert "ИТОГ" in lines[-1]


@pytest.mark.parametrize("mode,expect", [("ascii_only", {"ascii": True, "visible": False}),
                                         ("visible_only", {"ascii": False, "visible": True})])
def test_connect_tries_ascii_names_and_visible_excel(fake_excel, tmp_path, mode, expect):
    """Ошибка «неверный аргумент» у пользователя: пробуем латинские имена запросов и видимый Excel."""
    from hub.excel import com_connect
    xl = fake_excel["xl"] = FakeXL(mode)
    r = com_connect.connect_model(model_file(tmp_path), tmp_path / "М.csv", tmp_path / "bk", log=lambda m: None)
    assert not r["partial"]
    assert {k: r["way"][k] for k in expect} == expect
    wb = xl.books[0]
    names = [q.Name for q in wb.Queries]
    assert names == (["Hub_Packet", "Hub", "Hub_Series", "Hub_Updates"] if expect["ascii"]
                     else ["Хаб_Посылка", "Хаб", "Хаб_Ряды", "Хаб_Обновления"])
    if expect["ascii"]:
        assert "Hub_Packet" in next(q.Formula for q in wb.Queries if q.Name == "Hub")
    hub = next(ws for ws in wb.Worksheets if ws.Name == "Хаб")
    assert [lo.Name for lo in hub.ListObjects] == ["Хаб", "Хаб_Ряды"]      # таблицы — всегда по-русски
    upd = next(ws for ws in wb.Worksheets if ws.Name == "Хаб_Обновления")  # история обновлений — своим листом
    assert [lo.Name for lo in upd.ListObjects] == ["Хаб_Обновления"]
    assert xl.Visible is False                                             # Excel снова спрятан


def test_connect_starts_with_remembered_way(fake_excel, tmp_path):
    from hub.excel import com_connect
    fake_excel["xl"] = FakeXL("ascii_only")
    steps = []
    r = com_connect.connect_model(model_file(tmp_path), tmp_path / "М.csv", tmp_path / "bk", log=steps.append,
                                  prefer={"visible": False, "ascii": True, "way": "ListObjects.Add (по именам)"})
    assert r["way"]["way"] == "ListObjects.Add (по именам)"
    assert not any("не сработал" in s for s in steps)


def test_col_letter():
    from hub.excel.com_connect import col_letter
    assert [col_letter(n) for n in (1, 26, 27, 60)] == ["A", "Z", "AA", "BH"]


def test_explicit_args_way_is_tried_first(fake_excel, tmp_path):
    """Как у пользователя: работает только вызов со всеми аргументами — он должен сработать сразу."""
    from hub.excel import com_connect
    fake_excel["xl"] = FakeXL("explicit_only")
    steps = []
    r = com_connect.connect_model(model_file(tmp_path), tmp_path / "М.csv", tmp_path / "bk", log=steps.append)
    assert not r["partial"] and r["way"]["way"] == "ListObjects.Add (все аргументы явно)"
    assert r["way"]["ascii"] is False and r["way"]["visible"] is False


def test_connect_adds_file_sheets(fake_excel, tmp_path):
    """Листы файлов модели: запрос листа и «ячеек» → листы «ПБ_оценка» и «ПБ_оценка (ячейки)» с таблицами.
    Лист пользователя с тем же именем не трогается — тогда «… (хаб)»."""
    from hub.excel import com_connect
    fake_excel["xl"] = FakeXL("ok")
    sheets = [{"name": "ПБ_оценка", "cells": True}, {"name": "Модель", "cells": False}]
    r = com_connect.connect_model(model_file(tmp_path), tmp_path / "М.csv", tmp_path / "bk", log=lambda m: None,
                                  sheets=sheets)
    wb = fake_excel["xl"].books[0]
    assert not r["partial"]
    names = [q.Name for q in wb.Queries]
    assert "Хаб_Лист_ПБ_оценка" in names and "Хаб_Ячейки_ПБ_оценка" in names and "Хаб_Лист_Модель" in names
    formula = next(q.Formula for q in wb.Queries if q.Name == "Хаб_Лист_ПБ_оценка")
    assert " — лист ПБ_оценка.csv" in formula
    by = {ws.Name: [lo.Name for lo in ws.ListObjects] for ws in wb.Worksheets}
    assert by["ПБ_оценка"] == ["Лист_ПБ_оценка"] and by["ПБ_оценка (ячейки)"] == ["Ячейки_ПБ_оценка"]
    assert by["Модель"] == [] and by["Модель (хаб)"] == ["Лист_Модель"]        # лист пользователя цел


def test_stale_lock_file_does_not_block(fake_excel, tmp_path):
    """Брошенный «~$модель.xlsx» (Excel закрылся с ошибкой, файл пережил перезагрузку) не мешает подключению:
    файл свободен (переименовывается) — замок удаляется."""
    from hub.excel import com_connect
    fake_excel["xl"] = FakeXL("ok")
    m = model_file(tmp_path)
    lock = m.with_name("~$" + m.name)
    lock.write_bytes(b"owner")
    said = []
    r = com_connect.connect_model(m, tmp_path / "М.csv", tmp_path / "bk", log=said.append)
    assert not r["partial"] and not lock.exists() and any("брошенный" in s for s in said)


def test_really_busy_model_explains_why(fake_excel, tmp_path, monkeypatch):
    """Файл держит программа (переименовать нельзя): с замком Excel — «открыта в Excel», без него — объяснение про
    другие программы (Проводник, OneDrive, антивирус)."""
    from pathlib import Path
    from hub.core.errors import FileBusy
    from hub.excel import com_connect
    from hub.excel.models import connect_error_text
    m = model_file(tmp_path)
    real = Path.rename

    def busy(self, target):
        if self == m:
            raise PermissionError(32, "used by another process")
        return real(self, target)
    monkeypatch.setattr(Path, "rename", busy)
    assert com_connect.open_reason(m) == com_connect.BUSY_OTHER
    m.with_name("~$" + m.name).write_bytes(b"owner")
    with pytest.raises(FileBusy) as e:
        com_connect.connect_model(m, tmp_path / "М.csv", tmp_path / "bk", log=lambda s: None)
    assert connect_error_text(e.value) == com_connect.BUSY_EXCEL and m.with_name("~$" + m.name).exists()
