"""Подмена сетевых клиентов ЦБ и ЕМИСС ответами из samples/ (сайты из облака недоступны)."""
import copy
import json
from pathlib import Path

from hub.sources.cbr_api import CBR
from hub.sources.fedstat_api import Fedstat

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


class FakeCBR(CBR):
    """CBR, у которого _get читает образцы; /dataNew фильтруется по параметрам, как на сервере."""

    def __init__(self):
        super().__init__(pause=0)
        d = SAMPLES / "cbr"
        self.pubs = json.loads((d / "publications.json").read_text("utf-8"))
        self.cats = json.loads((d / "categoryNew.json").read_text("utf-8"))
        self.full = json.loads((d / "dataNew_all.json").read_text("utf-8"))
        self.datasets = {int(p.stem.split("_")[1]): json.loads(p.read_text("utf-8")) for p in d.glob("datasets_*.json")}
        self.calls = []
        self.fail = None

    def _get(self, path, params=(), retries=4):
        self.calls.append((path, list(params)))
        if self.fail:
            raise self.fail
        p = {}
        for k, v in params:
            p.setdefault(k, []).append(v)
        if path == "/publications":
            return copy.deepcopy(self.pubs)
        if path == "/categoryNew":
            return copy.deepcopy(self.cats)
        if path == "/datasets":
            return copy.deepcopy(self.datasets.get(int(p["publicationId"][0]), []))
        if path == "/dataNew":
            cat = int(p["categoryId"][0])
            inds = {c["indicator_id"] for c in self.cats["category"] if c["category_id"] == cat}
            y1, y2 = int(p["y1"][0]), int(p["y2"][0])
            want_i = {int(x) for x in p.get("i_ids", [])}
            want_m1 = {int(x) for x in p.get("m1_ids", [])}
            rows = []
            for r in self.full["RowData"]:
                import datetime as dt
                period_year = (dt.date.fromisoformat(r["date"][:10]) - dt.timedelta(days=1)).year
                if r["indicator_id"] in inds and y1 <= period_year <= y2 \
                        and (not want_i or r["indicator_id"] in want_i) \
                        and (not want_m1 or r["measure1_id"] in want_m1):
                    rows.append(copy.deepcopy(r))
            keys = {(r["indicator_id"], r["measure1_id"], r["measure2_id"], r["unit_id"]) for r in rows}
            links = [copy.deepcopy(l) for l in self.full["Links"]
                     if (l["indicator_id"], l["measure1_id"], l["measure2_id"], l["unit_id"]) in keys]
            return {"RowData": rows, "Links": links}
        if path == "/DatasetDescription":
            return [{"description": "<p>Синтетическое описание методологии.</p>"}]
        raise AssertionError(f"Неожиданный запрос {path}")

    def add_month(self, ind, m1, date_iso, value, unit_id=2):
        rid = max(r["id"] for r in self.full["RowData"]) + 1
        self.full["RowData"].append({"id": rid, "indicator_id": ind, "measure1_id": m1, "measure2_id": None,
                                     "unit_id": unit_id, "obs_val": value, "row_id": rid, "dt": "",
                                     "periodicity": "month", "date": date_iso + "T00:00:00", "digits": 2})


class FakeFedstat(Fedstat):
    def __init__(self):
        super().__init__(pause=0)
        self.page = (SAMPLES / "fedstat" / "indicator_31074.html").read_bytes()
        self.sdmx = (SAMPLES / "fedstat" / "data_31074.sdmx.xml").read_bytes()
        self.calls = []
        self.downloads = []
        self.fail = None

    def _get(self, url, data=None, retries=4):
        self.calls.append((url, data))
        if self.fail:
            raise self.fail
        if "/indicator/downloadData.do" in url:
            return "application/xml", self.filtered(data)
        if "/indicator/" in url:
            return "text/html;charset=UTF-8", self.page
        raise AssertionError(url)

    OKATO = {"1558883": "643", "1558900": "030"}

    max_years = None      # «сервер не справляется», если в запросе больше лет
    max_regions_years = None   # … или больше сочетаний «регион × год»

    def filtered(self, data):
        """Как сервер: только выбранные регионы (измерение 0) и годы (измерение 3)."""
        import re
        import urllib.error
        chosen = {self.OKATO[v.split("_", 1)[1]] for k, v in data if k == "selectedFilterIds" and v.startswith("0_")}
        years = {v.split("_", 1)[1] for k, v in data if k == "selectedFilterIds" and v.startswith("3_")}
        if self.max_years and len(years) > self.max_years:
            raise urllib.error.HTTPError("https://www.fedstat.ru", 500, "too big", {}, None)
        if self.max_regions_years and len(chosen) * len(years) > self.max_regions_years:
            raise urllib.error.HTTPError("https://www.fedstat.ru", 500, "too big", {}, None)
        self.downloads.append(sorted(years))
        text = self.sdmx.decode("utf-8")

        def keep(m):
            okato = re.search(r'concept="s_OKATO" value="(\d+)"', m.group(0)).group(1)
            if okato not in chosen:
                return ""
            return re.sub(r"<generic:Obs><generic:Time>(\d{4})</generic:Time>.*?</generic:Obs>",
                          lambda o: o.group(0) if o.group(1) in years else "", m.group(0), flags=re.S)
        return re.sub(r"<generic:Series>.*?</generic:Series>", keep, text, flags=re.S).encode("utf-8")
