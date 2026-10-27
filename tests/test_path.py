"""«Путь к файлу»: шаги по ссылкам от стартовой страницы до файла (Минфин: свежая карточка → «Скачать»)."""
import os

import pytest

import minfin_site as MS
from hub.sources import _path as PT


def get_page_for(site, base):
    def get(url, need):
        path = url.replace(base, "")
        if path.startswith("/ru/statistics/fedbud"):
            return MS.list_page(site.releases), url
        key = path.split("id_4=")[-1]
        return MS.doc_page(key[0], key[1:]), url
    return get


def test_links_have_card_text_and_steps_find_fresh_file():
    base = "https://minfin.gov.ru"
    lk = PT.links(MS.list_page(MS.RELEASES), base + "/ru/statistics/fedbud/")
    icon = [x for x in lk if x["href"].endswith("m2026-10-09.xlsx")][0]
    assert icon["text"] == "" and "ежемесячная" in icon["near"] and "09.10.2026" in icon["near"]
    clicks = [{"text": "", "near": icon["near"], "href": icon["href"]}]
    steps = PT.guess_steps(clicks)
    assert steps == [{"words": "", "near": "краткая ежемесячная информация исполнении",
                      "pick": "newest", "href": ".xlsx"}]
    html = MS.list_page(MS.RELEASES)            # с сохранённой страницей — слова, отличающие карточку от других
    assert PT.guess_steps([{**clicks[0], "html": html, "page": base}])[0]["near"] == steps[0]["near"]
    site = MS.Site()
    path = {"start": base + "/ru/statistics/fedbud/", "steps": steps}
    assert PT.follow(path, get_page=get_page_for(site, base)).endswith("/m2026-10-09.xlsx")
    site.releases.insert(0, ("2026-11-10", "m", MS.RELEASES[1][2]))   # через месяц — новая карточка сверху
    assert PT.follow(path, get_page=get_page_for(site, base)).endswith("/m2026-11-10.xlsx")


def test_two_steps_via_document_page_and_clear_error():
    base = "https://minfin.gov.ru"
    clicks = [{"text": "Документ", "near": "Документ Краткая ежемесячная информация об исполнении федерального "
                                            "бюджета (млрд руб.) 09.10.2026"},
              {"text": "Скачать (xlsx, 25 Кб)", "near": "Документ Опубликовано 09.10.2026 Назад Скачать"}]
    steps = PT.guess_steps(clicks)
    assert steps[0]["words"] == "документ" and "ежемесячная" in steps[0]["near"]
    assert steps[1]["words"] == "скачать xlsx"
    trace = []
    url = PT.follow({"start": base + "/ru/statistics/fedbud/", "steps": steps},
                    get_page=get_page_for(MS.Site(), base), trace=trace)
    assert url.endswith("/m2026-10-09.xlsx") and len(trace) == 2 and trace[0][1] == 1
    from hub.core.errors import StructureError
    with pytest.raises(StructureError, match="Шаг 1"):
        PT.follow({"start": base + "/ru/statistics/fedbud/", "steps": [{"words": "нет такой", "pick": "first"}]},
                  get_page=get_page_for(MS.Site(), base))


def test_file_sheets_job_follows_path(runner, monkeypatch):
    """Показатель «листы файла» с путём: при обновлении хаб проходит шаги и берёт свежий файл."""
    from hub.sources import _web as W
    site = MS.Site()
    base = "https://minfin.gov.ru"

    def fake_get(url, ctx=None, timeout=60, headers=None):
        path = url.replace(base, "")
        if path.startswith("/common/upload/library/"):
            return 200, "application/vnd.ms-excel", MS.xlsx(path.rsplit("/", 1)[-1]), url
        text, _u = get_page_for(site, base)(url, None)
        return 200, "text/html; charset=utf-8", text.encode(), url
    monkeypatch.setattr(W, "http_get", fake_get)
    params = {"path": {"start": base + "/ru/statistics/fedbud/",
                       "steps": [{"words": "", "near": "ежемесячная информация", "pick": "newest"}]},
              "mode": "append", "sheets": [{"title": "Исполнение ФБ", "index": 0, "count": 1, "name": "ФБ_месяц"}]}
    job = runner.cfg.add_job("file_sheets", "Минфин: ФБ", params)
    o = runner.run(job_ids=[job["id"]]).outcomes[0]
    assert o.status == "updated", o.message
    site.releases.insert(0, ("2026-11-10", "m", MS.RELEASES[1][2]))
    o = runner.run(job_ids=[job["id"]]).outcomes[0]
    assert o.status == "updated"
    from hub.core import sheets as SH
    text = SH.sheet_csv(runner.paths, job["id"], "ФБ_месяц").read_text("utf-8-sig")
    assert "m2026-10-09.xlsx" in text and "m2026-11-10.xlsx" in text


@pytest.mark.skipif(not os.environ.get("HUB_CHROMIUM"), reason="нужен Chromium (HUB_CHROMIUM)")
def test_record_path_in_browser(tmp_path):
    """Окно записи: щелчок по значку скачивания в карточке → записан щелчок (без текста, с плашкой) и скачивание."""
    pytest.importorskip("playwright")
    from hub.core.paths import Paths
    from hub.sources.browser import Browser
    site = MS.Site()
    srv, base = site.start()
    try:
        def act(page):
            page.locator("a.dl").nth(1).click()
        got = Browser(Paths(tmp_path)).record_path(base + "/ru/statistics/fedbud/", headless=True, on_page=act)
        assert got["download"] and got["download"].endswith("m2026-10-09.xlsx")
        c = got["clicks"][0]
        assert c["text"] == "" and "ежемесячная" in c["near"]
        steps = PT.guess_steps(got["clicks"])
        assert PT.follow({"start": base + "/ru/statistics/fedbud/", "steps": steps}).endswith("m2026-10-09.xlsx")
    finally:
        srv.shutdown()
