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
    from hub.core.paths import Paths, set_workdir
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
    params, name = app.find.cbr.current_params()
    assert params["category"] == 18 and params["indicators"] == [37]
    app.find.q.set("ставки вклад")
    app.find.search()
    kinds = {app.find.results.item(i, "values")[0] for i in app.find.results.get_children()}
    assert "Банк России" in kinds


def test_fedstat_nav_params(app, fakes, tmp_path):
    nav = app.find.fedstat
    nav.fs = fakes["fedstat"]
    nav.cat = {"roots": [], "years": {}}
    nav.run_bg(lambda: nav.fs.indicator("31074"), nav._on_indicator, "…")
    assert pump(app, 5, until=lambda: str(nav.add_btn.cget("state")) == "normal")
    chosen = nav.dim_lists["0"][0]
    chosen.discard("1558900")
    params, name = nav.current_params()
    assert params == {"id": 31074, "select": {"0": ["1558883"]}}
    assert "Российская Федерация" in name
