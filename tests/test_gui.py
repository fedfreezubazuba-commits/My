"""Окно собирается и основные сценарии проходят (нужен дисплей: xvfb-run -a pytest)."""
import os
import time

import pytest

tk = pytest.importorskip("tkinter")
pytestmark = pytest.mark.skipif(not os.environ.get("DISPLAY") and os.name != "nt", reason="нет дисплея")


def pump(app, seconds=3.0, until=None):
    end = time.time() + seconds
    while time.time() < end:
        app.root.update()
        if until and until():
            return True
        time.sleep(0.02)
    return False


@pytest.fixture
def app(workdir, fakes, monkeypatch):
    from hub.gui import app as A
    from hub.core.config import Config
    from hub.core.paths import Paths
    monkeypatch.setattr("hub.core.paths.POINTER", workdir / "pointer.txt")
    Config(Paths(workdir).ensure()).update_settings(first_run_done=True)
    shown = []
    from tkinter import messagebox
    for fn in ("showinfo", "showwarning", "showerror"):
        monkeypatch.setattr(messagebox, fn, lambda *a, _fn=fn, **k: shown.append((_fn, a)))
    for fn in ("askyesno", "askokcancel"):
        monkeypatch.setattr(messagebox, fn, lambda *a, **k: True)
    root = tk.Tk()
    a = A.HubApp(root)
    a.clients = fakes
    a.find.cbr.cbr = fakes["cbr"]
    a.find.fedstat.fs = fakes["fedstat"]
    a.find.fedstat.loaded = True      # каталог ЕМИСС — это 20 МБ с сайта; в тесте не грузим
    a.shown = shown
    pump(a, 0.5)
    yield a
    root.destroy()


def test_all_tabs_build(app):
    for tab in app.nb.tabs():
        app.nb.select(tab)
        pump(app, 0.2)
    assert app.overview.big.cget("text") == "Добавьте первый показатель"


def test_add_flow_model_and_formula(app, fakes):
    from hub.gui.dialog_add import AddDialog
    from hub.gui.tab_jobs import FormulaWindow
    app.cfg.add_model("Сбербанк", str(app.paths.root / "Сбербанк.xlsx"))
    d = AddDialog(app, "cbr", {"category": 18, "indicators": [37], "m1": [2]}, "Ставки по вкладам")
    d.model_vars["Сбербанк"].set(True)
    d.add()
    assert pump(app, 10, until=lambda: "Получено" in d.result.cget("text"))
    assert "1 ряд" in d.result.cget("text")
    assert app.store.subscribed("Сбербанк") == ["CBR_18_37_m1_2"]
    app.refresh_all()
    assert app.jobs.tree.get_children()
    app.jobs.select(d.job["id"])
    w = FormulaWindow(app, ["CBR_18_37_m1_2"])
    pump(app, 0.2)
    entries = [c for c in w.body.winfo_children() if c.winfo_class() == "TFrame"]
    assert entries
    w.destroy()
    app.models.refresh()
    assert app.models.tree.exists("Сбербанк")
    assert app.models.series.exists("CBR_18_37_m1_2")
    d.destroy()


def test_navigators_with_fake_cbr(app, fakes):
    app.find.cbr.cbr = fakes["cbr"]
    app.find.nb.select(app.find.cbr)
    assert pump(app, 10, until=lambda: app.find.cbr.roots)
    app.find.cbr.select_indicator(18, 37, "Средневзвешенные процентные ставки по вкладам физических лиц в рублях")
    assert pump(app, 10, until=lambda: str(app.find.cbr.add_btn.cget("state")) == "normal")
    nav = app.find.cbr
    params, name = nav.current_params()
    assert params["category"] == 18 and params["indicators"] == [37] and "y1" not in params
    # годы выбираются списком, как в ЕМИСС: снимаем 2024 → грузим с 2025
    chosen, all_ids, tab, title = nav.lists["y"]
    assert title == "Год" and all_ids == [2024, 2025]
    chosen.discard(2024)
    nav._update_counts()
    assert nav.current_params()[0]["y1"] == 2025
    assert "лет: 1" in nav.sel_var.get()
    assert nav.nb.tab(tab, "text") == "Год (1/2)"
    app.find.q.set("ставки вклад")
    app.find.search()
    kinds = {app.find.results.item(i, "values")[0] for i in app.find.results.get_children()}
    assert "Банк России" in kinds


def test_fedstat_nav_params(app, fakes, tmp_path):
    nav = app.find.fedstat
    nav.fs = fakes["fedstat"]
    nav.cat = {"roots": [], "years": {}}
    nav.run_bg(lambda: nav.fs.indicator("31074"), nav._on_indicator, "…")
    assert pump(app, 20, until=lambda: str(nav.add_btn.cget("state")) == "normal")
    chosen = nav.dim_lists["0"][0]
    chosen.discard("1558900")
    params, name = nav.current_params()
    assert params == {"id": 31074, "select": {"0": ["1558883"]}}
    assert "Российская Федерация" in name


def test_site_wizard_offline(app, monkeypatch):
    from pathlib import Path
    from hub.sources import _web as W
    web = Path(__file__).resolve().parents[1] / "samples" / "web"
    monkeypatch.setattr(W, "http_get", lambda url, ctx=None, timeout=60: (200, "text/html",
                                                                          (web / "metro_losos.html").read_bytes(), url))
    site = app.find.site
    app.find.nb.select(site)
    site.url.set("https://online.metro-cc.ru/products/losos-murmanskij-6-7kg-inarctica?utm_source=x")
    site.analyze()
    assert pump(app, 15, until=lambda: site.found is not None)
    assert "utm_source" not in site.url.get()
    assert len(site.values.get_children()) == 3
    assert str(site.add_btn.cget("state")) == "normal"
    site.add()
    pump(app, 0.3)
    dialogs = [w for w in app.root.winfo_children() if w.winfo_class() == "Toplevel"]
    assert dialogs and dialogs[-1].name.get().startswith("Лосось")
    dialogs[-1].destroy()


def test_file_tab_preview(app, tmp_path, monkeypatch):
    import datetime as dt
    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.title = "Нефть"
    ws.append(["Дата", "Брент"])
    ws.append([dt.datetime(2026, 9, 1), 80.0])
    ws.append([dt.datetime(2026, 10, 1), 82.0])
    f = tmp_path / "выгрузка.xlsx"
    wb.save(f)
    from tkinter import filedialog
    monkeypatch.setattr(filedialog, "askopenfilename", lambda **k: str(f))
    tab = app.find.file
    tab.load_inbox()
    pump(app, 0.3)
    assert tab.list.get_children() and tab.mode.get() == "history"
    tab.add()
    pump(app, 0.3)
    assert (app.paths.inbox / "vygruzka" / "выгрузка.xlsx").exists()


def test_settings_examples_and_exports(app, fakes):
    from hub.excel import exports
    app.cfg.add_job("cbr", "Ставки", {"category": 18, "indicators": [37], "m1": [2]})
    r = app.runner()
    r.run(force=True)
    r.close()
    path = exports.export_job(app.cfg, app.store, app.paths, app.cfg.jobs()[0]["id"])
    from openpyxl import load_workbook
    wb = load_workbook(path)
    assert wb.sheetnames == ["Данные", "Последние", "Длинная", "Паспорт"]
    data = wb["Данные"]
    assert [data.cell(r, 2).value for r in range(1, 5)] == ["Показатель", "Разрез 1", "Единицы", "Период"]
    assert data["C4"].value == "CBR_18_37_m1_2" and data["C3"].value == "% годовых"
    long = [c.value for c in wb["Длинная"][1]]
    assert long == ["Дата", "Период", "Код", "Показатель", "Разрез 1", "Единицы", "Значение"]
    assert wb["Длинная"]["E2"].value.startswith("до 1 года")
    files = exports.update_job_files(app.cfg, app.store, app.paths, app.cfg.jobs())
    assert all(v.endswith(".xlsx") for v in files.values())
