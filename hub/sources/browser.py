"""
Встроенный браузер (Playwright + Chromium) с постоянным профилем в рабочей папке
(browser_profile/): куки и входы на сайты сохраняются между запусками.

  get_html(url)  — открыть страницу без окна и вернуть HTML после выполнения скриптов;
  login(url)     — открыть окно: пользователь сам входит на сайт (или выбирает магазин) и
                   закрывает окно. Паролей хаб не видит и не хранит;
  pick(url)      — окно с подсветкой элементов: пользователь щёлкает по числу или таблице,
                   хаб получает селектор, XPath, текст и (для таблицы) её HTML.

Playwright необязателен: без него всё остальное работает, а эти функции сообщают,
что браузер не установлен. Путь к Chromium можно задать переменной HUB_CHROMIUM.
"""
import os
import re
import threading
import time

from ..core import errors as E

_lock = threading.Lock()

PICKER_JS = r"""
(() => {
  if (document.getElementById('__hub_box')) return;
  window.__hubPick = window.__hubPick || null;
  const box = document.createElement('div');
  box.id = '__hub_box';
  box.style.cssText = 'position:fixed;pointer-events:none;z-index:2147483646;border:2px solid #1565c0;' +
                      'background:rgba(21,101,192,.08);border-radius:3px;transition:all .05s';
  const tip = document.createElement('div');
  tip.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#1565c0;color:#fff;' +
                      'font:15px Segoe UI,Arial;padding:8px 14px;text-align:center';
  tip.textContent = 'Хаб данных: щёлкните по нужному числу — или по всему блоку с таблицей (Shift+щелчок — выбрать таблицу целиком).';
  document.documentElement.appendChild(box);
  document.documentElement.appendChild(tip);
  const cssPath = (el) => {
    if (el.id && /^[A-Za-z][\w-]*$/.test(el.id)) return '#' + el.id;
    const parts = [];
    while (el && el.nodeType === 1 && el !== document.body && parts.length < 8) {
      let sel = el.tagName.toLowerCase();
      const cls = [...el.classList].filter(c => /^[A-Za-z][\w-]*$/.test(c) && !/active|hover|focus|selected/.test(c)).slice(0, 2);
      if (cls.length) sel += '.' + cls.join('.');
      const p = el.parentElement;
      if (p) {
        const same = [...p.children].filter(c => c.tagName === el.tagName);
        if (same.length > 1) sel += ':nth-of-type(' + (same.indexOf(el) + 1) + ')';
      }
      parts.unshift(sel);
      if (el.id && /^[A-Za-z][\w-]*$/.test(el.id)) { parts[0] = '#' + el.id; break; }
      el = p;
    }
    return parts.join(' > ');
  };
  const xPath = (el) => {
    const parts = [];
    while (el && el.nodeType === 1) {
      let i = 1, s = el.previousElementSibling;
      while (s) { if (s.tagName === el.tagName) i++; s = s.previousElementSibling; }
      parts.unshift(el.tagName.toLowerCase() + '[' + i + ']');
      el = el.parentElement;
    }
    return '/' + parts.join('/');
  };
  const labelBefore = (el) => {
    let s = el.previousElementSibling, p = el.parentElement, n = 0;
    while (n < 4) {
      while (s) { const t = (s.innerText || '').trim(); if (t && t.length < 80 && !/\d{3}/.test(t) && !/click|нажмите/i.test(t)) return t; s = s.previousElementSibling; }
      if (!p) break; s = p.previousElementSibling; p = p.parentElement; n++;
    }
    return '';
  };
  const target = (e) => e.shiftKey ? (e.target.closest('table') || e.target.parentElement.parentElement || e.target) : e.target;
  document.addEventListener('mouseover', (e) => {
    const r = target(e).getBoundingClientRect();
    box.style.left = r.left + 'px'; box.style.top = r.top + 'px';
    box.style.width = r.width + 'px'; box.style.height = r.height + 'px';
  }, true);
  document.addEventListener('click', (e) => {
    e.preventDefault(); e.stopPropagation();
    const el = target(e);
    const txt = (el.innerText || '').trim();
    // щелчок по блоку, в котором много чисел (таблица, «плитки» со ставками), — это выбор таблицы
    const nums = (txt.match(/\d[\d\s.,]*/g) || []).length;
    const block = e.shiftKey || nums > 3;
    window.__hubPick = {
      kind: block ? 'table' : 'value',
      selector: cssPath(el), xpath: xPath(el), text: txt.slice(0, 2000),
      label: labelBefore(el), html: el.outerHTML.slice(0, 300000), url: location.href
    };
    tip.textContent = 'Выбрано: ' + window.__hubPick.text.slice(0, 80) + ' — можно закрыть это окно.';
    tip.style.background = '#2e7d32';
  }, true);
})();
"""

LOGIN_BANNER_JS = r"""
(() => {
  if (document.getElementById('__hub_banner')) return;
  const b = document.createElement('div');
  b.id = '__hub_banner';
  b.style.cssText = 'position:fixed;bottom:0;left:0;right:0;z-index:2147483647;background:#1565c0;color:#fff;' +
                    'font:15px Segoe UI,Arial;padding:8px 14px;text-align:center';
  b.textContent = '%s';
  document.documentElement.appendChild(b);
})();
"""


FORM_BANNER_JS = r"""
(() => {
  if (document.getElementById('__hub_banner')) return;
  const b = document.createElement('div');
  b.id = '__hub_banner';
  b.style.cssText = 'position:fixed;bottom:0;left:0;right:0;z-index:2147483647;background:#1565c0;color:#fff;' +
                    'font:15px Segoe UI,Arial;padding:8px 14px;text-align:center;pointer-events:none';
  b.textContent = 'Хаб данных: откройте на сайте нужные данные. Если есть форма — заполните её (лучше ОДИН месяц: годы и ' +
                  'месяцы хаб переберёт сам) и нажмите поиск; если сайт покажет ползунок — сдвиньте его. ' +
                  'Когда таблица или график с данными появится — закройте это окно.';
  document.documentElement.appendChild(b);
})();
"""

# запрос из страницы сайта — так же, как его отправляет сама форма (со скриптами и куками сайта)
XHR_JS = r"""
async (a) => await new Promise((resolve) => {
  const x = new XMLHttpRequest();
  x.open(a.method, a.url, true);
  for (const [k, v] of Object.entries(a.headers || {})) { try { x.setRequestHeader(k, v); } catch (e) {} }
  x.timeout = a.timeout;
  x.onload = () => resolve({status: x.status, text: x.responseText || ''});
  x.onerror = () => resolve({status: 0, text: ''});
  x.ontimeout = () => resolve({status: -1, text: ''});
  x.send(a.method === 'GET' || a.method === 'HEAD' ? null : (a.body || ''));
})
"""

CHECK_MESSAGE = ("Сайт просит пройти проверку «я не робот» (ползунок). Хаб не проходит её сам: нажмите на «Обзоре» "
                 "«Пройти проверку» или обновите показатель кнопкой — окно браузера откроется, сдвиньте ползунок")
CHECK_MARKERS = ("滑块", "滑动验证", "向右滑动", "拖动滑块", "请完成安全验证", "slide to verify",
                 "slider-verify", "nc_wrapper", "nc-container", "geetest", "tcaptcha", "verify-slider")
# просто слово «captcha» есть и на обычных больших страницах (форма обратной связи cbr.ru) — это признак проверки,
# только если сама страница маленькая (страница-проверка), а не весь сайт с меню и таблицами
GENERIC_MARKERS = ("captcha",)
SMALL_PAGE = 40000

CHECK_BANNER_JS = r"""
(() => {
  if (document.getElementById('__hub_banner')) return;
  const b = document.createElement('div');
  b.id = '__hub_banner';
  b.style.cssText = 'position:fixed;top:0;left:0;right:0;z-index:2147483647;background:#e65100;color:#fff;' +
                    'font:15px Segoe UI,Arial;padding:8px 14px;text-align:center;pointer-events:none';
  b.textContent = 'Хаб данных: сайт просит проверку «я не робот». Сдвиньте ползунок — дальше хаб продолжит сам ' +
                  '(эта вкладка закроется). Если проверки не видно — просто закройте вкладку.';
  document.documentElement.appendChild(b);
})();
"""

CHECK_BANNER_FORM_JS = CHECK_BANNER_JS.replace(
    "Сдвиньте ползунок — дальше хаб продолжит сам ' +\n                  '(эта вкладка закроется). Если проверки не видно — просто закройте вкладку.",
    "Нажмите поиск в форме, сдвиньте ползунок, ' +\n                  'дождитесь таблицы и закройте эту вкладку — дальше хаб продолжит сам.")

KEEP_HEADERS = ("x-requested-with", "accept")
MAX_TEXT = 3_000_000


LOGIN_TEXT = "Хаб данных: войдите на сайт (или выберите магазин) как обычно, затем просто закройте это окно."
LOGIN_BANNER_JS = LOGIN_BANNER_JS.replace("%s", LOGIN_TEXT)


class Browser:
    def __init__(self, paths):
        self.profile = paths.browser_profile if paths else None

    @staticmethod
    def available() -> bool:
        try:
            import playwright.sync_api  # noqa: F401
            return True
        except ImportError:
            return False

    def _launch(self, p, headless, offscreen=False):
        if self.profile is None:
            raise E.HubError(E.INTERNAL, "Не задана рабочая папка для профиля браузера")
        self.profile.mkdir(parents=True, exist_ok=True)
        from ._web import UA
        # обычный браузер, а не «робот»: некоторые сайты не показывают данные «безголовому» Chrome
        args = ["--disable-blink-features=AutomationControlled"]
        kw = {"headless": headless, "locale": "ru-RU", "user_agent": UA, "args": args, "accept_downloads": True}
        if offscreen:
            args += ["--window-position=-32000,-32000", "--window-size=1280,900"]
        if headless or offscreen:
            kw["viewport"] = {"width": 1280, "height": 900}
        else:
            # окно для человека: страница растягивается вместе с окном (кнопка «развернуть» работает);
            # место — явно на экране: профиль общий с окнами «за краем экрана» и помнит их положение
            args += ["--window-size=1280,900", "--window-position=80,60"]
            kw["no_viewport"] = True
        exe = os.environ.get("HUB_CHROMIUM")
        if exe:
            kw["executable_path"] = exe
        try:
            return p.chromium.launch_persistent_context(str(self.profile), **kw)
        except Exception as e:  # noqa: BLE001
            msg = str(e)
            if "Executable doesn't exist" in msg or "playwright install" in msg:
                raise E.HubError(E.INTERNAL, "Встроенный браузер не скачан — запустите «Установка.bat» ещё раз")
            if "SingletonLock" in msg or "ProcessSingleton" in msg or "already in use" in msg:
                raise E.HubError(E.INTERNAL, "Встроенный браузер уже открыт — закройте его окно и повторите")
            raise

    def _sync(self):
        if not self.available():
            raise E.HubError(E.INTERNAL, "Встроенный браузер не установлен — запустите «Установка.bat» ещё раз")
        from playwright.sync_api import sync_playwright
        return sync_playwright()

    def get_html(self, url, wait_ms=2500, timeout_ms=60000, offscreen=False):
        """HTML после выполнения скриптов. offscreen=True — настоящее окно за краем экрана
        (для сайтов, которые не отдают данные браузеру без окна)."""
        with _lock, self._sync() as p:
            ctx = self._launch(p, headless=not offscreen, offscreen=offscreen)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)
                except Exception as e:  # noqa: BLE001
                    raise E.HubError(E.NETWORK, "Встроенный браузер не смог открыть сайт", detail=str(e))
                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except Exception:  # noqa: BLE001 — сайты с вечными запросами
                    pass
                page.wait_for_timeout(wait_ms)
                # ждём, пока текст страницы перестанет расти (данные подгружаются скриптами)
                prev, stable, end = -1, 0, time.time() + 20
                while time.time() < end and stable < 2:
                    try:
                        n = page.evaluate("document.body ? document.body.innerText.length : 0")
                    except Exception:  # noqa: BLE001
                        n = prev
                    stable = stable + 1 if n == prev else 0
                    prev = n
                    page.wait_for_timeout(700)
                return page.content(), page.url
            finally:
                ctx.close()

    def download(self, url, headless=False, timeout_ms=120000):
        """Файл по прямой ссылке — так же, как его скачивает браузер (окно за краем экрана): для сайтов, которые
        не отдают файл обычной программе (защита, сертификаты). → (байты, итоговый адрес, тип содержимого)."""
        with _lock, self._sync() as p:
            ctx = self._launch(p, headless=headless, offscreen=not headless)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                for attempt in range(2):
                    got = {}
                    page.on("download", lambda d: got.setdefault("d", d))
                    resp = None
                    try:
                        resp = page.goto(url, wait_until="commit", timeout=timeout_ms)
                    except Exception as e:  # noqa: BLE001 — «Download is starting»: файл скачивается, а не открывается
                        if "download" not in str(e).lower() and "ERR_ABORTED" not in str(e):
                            raise E.HubError(E.NETWORK, "Встроенный браузер не смог скачать файл", detail=str(e))
                    end = time.time() + 15
                    while "d" not in got and resp is None and time.time() < end:
                        page.wait_for_timeout(200)
                    if "d" in got:
                        from pathlib import Path
                        path = got["d"].path()
                        return Path(path).read_bytes(), url, ""
                    if resp is None:
                        raise E.HubError(E.NETWORK, "Встроенный браузер не получил файл")
                    try:
                        page.wait_for_load_state("load", timeout=30000)
                    except Exception:  # noqa: BLE001
                        pass
                    ctype = (resp.headers.get("content-type") or "").lower()
                    if "html" in ctype:
                        # страница защиты: скрипт проверяет браузер и сам отдаёт файл — ждём скачивания
                        end = time.time() + 25
                        while "d" not in got and time.time() < end:
                            page.wait_for_timeout(300)
                    if "d" in got:
                        from pathlib import Path
                        return Path(got["d"].path()).read_bytes(), url, ""
                    body = resp.body()
                    if "html" in ctype and attempt == 0 and _block_category(resp.status, body[:30000].decode(
                            "utf-8", "ignore")):
                        _settle(page)              # страница защиты: она ставит куку и пускает дальше — ещё раз
                        page.wait_for_timeout(3000)
                        continue
                    return body, resp.url, ctype
                raise E.HubError(E.BOT, "Сайт показывает проверку вместо файла")
            finally:
                try:
                    ctx.close()
                except Exception:  # noqa: BLE001
                    pass

    def login(self, url, timeout_s=900, banner=None):
        """Окно для входа (или для проверки «я не робот»). Возвращается, когда пользователь закрыл окно
        (или через 15 минут): {"cookies": [...], "user_agent": "..."} — чтобы хаб мог ходить на сайт и без
        браузера (ЕМИСС). Пароли не читаются и не сохраняются — только куки сайта, как в самом браузере."""
        got = {"cookies": [], "user_agent": ""}
        with _lock, self._sync() as p:
            ctx = self._launch(p, headless=False)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                _show_window(page)
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                try:
                    got["user_agent"] = page.evaluate("navigator.userAgent")
                except Exception:  # noqa: BLE001
                    pass
                end = time.time() + timeout_s
                while time.time() < end and any(not pg.is_closed() for pg in ctx.pages):
                    for pg in ctx.pages:
                        try:
                            pg.evaluate(LOGIN_BANNER_JS if not banner else
                                        LOGIN_BANNER_JS.replace(LOGIN_TEXT, banner.replace("'", "’")))
                        except Exception:  # noqa: BLE001 — страница грузится или закрыта
                            pass
                    try:
                        got["cookies"] = ctx.cookies(url)
                    except Exception:  # noqa: BLE001
                        pass
                    time.sleep(1)
            finally:
                try:
                    ctx.close()
                except Exception:  # noqa: BLE001
                    pass
        return got

    def record(self, url, timeout_s=1800, headless=False, on_page=None):
        """Окно, в котором пользователь заполняет форму и нажимает поиск. Хаб записывает запросы страницы
        (адрес, поля формы, ответ). Возвращается, когда окно закрыли: [{method, url, body, content_type,
        headers, resource_type, frame_url, text}] — последние запросы, вернувшие страницу или данные."""
        cands = []

        def on_response(resp):
            try:
                req = resp.request
                if req.resource_type not in ("document", "xhr", "fetch") or resp.status != 200:
                    return
                ct = (resp.headers.get("content-type") or "").lower()
                if ct and not any(x in ct for x in ("html", "json", "text/plain", "xml")):
                    return
                try:
                    frame_url = req.frame.url
                except Exception:  # noqa: BLE001
                    frame_url = ""
                hdr = req.headers
                cands.append({"resp": resp, "method": req.method, "url": req.url, "body": req.post_data or "",
                              "content_type": hdr.get("content-type", ""),
                              "headers": {k: hdr[k] for k in KEEP_HEADERS if hdr.get(k)},
                              "resource_type": req.resource_type, "frame_url": frame_url})
                del cands[:-80]
            except Exception:  # noqa: BLE001
                pass

        def read_bodies():
            for c in cands:
                if "text" not in c:
                    try:
                        c["text"] = c["resp"].text()[:MAX_TEXT]
                    except Exception:  # noqa: BLE001 — страница ушла дальше, тело ответа уже недоступно
                        c["text"] = ""

        with _lock, self._sync() as p:
            ctx = self._launch(p, headless=headless)
            ctx.on("response", on_response)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                if not headless:
                    _show_window(page)
                page.goto(url, wait_until="domcontentloaded", timeout=90000)
                if on_page:
                    on_page(page)              # для тестов: заполнить форму программно
                    read_bodies()
                else:
                    end = time.time() + timeout_s
                    while time.time() < end and any(not pg.is_closed() for pg in ctx.pages):
                        read_bodies()
                        for pg in ctx.pages:
                            try:
                                pg.evaluate(FORM_BANNER_JS)
                            except Exception:  # noqa: BLE001
                                pass
                        time.sleep(0.4)
                    read_bodies()
            finally:
                try:
                    ctx.close()
                except Exception:  # noqa: BLE001
                    pass
        return [{k: v for k, v in c.items() if k != "resp"} for c in cands if c.get("text")]

    def replay(self, start_url, requests, pause_s=4, progress=None, headless=False, timeout_ms=120000,
               interactive=False, human=None, human_wait_s=600):
        """Повторить записанные запросы формы из страницы сайта (окно за краем экрана): сайт открывается
        как у человека, со своими скриптами и защитой, запросы идут по одному с паузой. → [текст ответа].

        Если сайт просит проверку «я не робот» (ползунок): interactive=True (обновление по кнопке, человек у
        компьютера) — окно выходит на экран, человек проходит проверку, хаб продолжает; иначе — ошибка BOT,
        и на «Обзоре» появляется «Пройти проверку». Решать проверку за человека хаб не пытается.
        human — для тестов: «действие человека» в открытой вкладке."""
        out = []
        with _lock, self._sync() as p:
            ctx = self._launch(p, headless=headless, offscreen=not headless)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                try:
                    page.goto(start_url, wait_until="domcontentloaded", timeout=timeout_ms)
                except Exception as e:  # noqa: BLE001
                    raise E.HubError(E.NETWORK, "Встроенный браузер не смог открыть сайт", detail=str(e))
                _settle(page)
                for i, req in enumerate(requests, 1):
                    if progress:
                        progress(i)
                    checks = 0
                    while True:
                        status, text = self._xhr(page, req, timeout_ms)
                        if status == 412:                  # защита сайта обновляет свои куки — перезагрузить страницу
                            page.reload(wait_until="domcontentloaded", timeout=timeout_ms)
                            _settle(page)
                            status, text = self._xhr(page, req, timeout_ms)
                        if not is_check(status, text, page):
                            break
                        checks += 1
                        if not interactive or checks > 2:
                            raise E.HubError(E.BOT, CHECK_MESSAGE)
                        if progress:
                            progress(i, "Сайт просит пройти проверку «я не робот» — сдвиньте ползунок в окне браузера")
                        self._human_check(ctx, page, req, start_url, second=checks > 1, human=human,
                                          wait_s=human_wait_s)
                    cat = _block_category(status, text)
                    if cat:
                        raise E.HubError(cat, f"Сайт не выдал данные (ответ {status})")
                    if status == -1:
                        raise E.HubError(E.NETWORK, f"Сайт не ответил за {timeout_ms // 60000} мин")
                    if status != 200:
                        raise E.HubError(E.NETWORK, f"Сайт ответил ошибкой {status}" if status else "Нет связи с сайтом")
                    out.append(text)
                    if i < len(requests):
                        page.wait_for_timeout(int(pause_s * 1000))
            finally:
                try:
                    ctx.close()
                except Exception:  # noqa: BLE001
                    pass
        return out

    def capture(self, start_url, req, headless=False, timeout_ms=90000):
        """Открыть страницу (окно за краем экрана) и взять тот же ответ с данными, который страница запрашивает
        сама при открытии, — со всеми её ключами доступа. → текст ответа или None, если страница его не запросила."""
        from urllib.parse import urlsplit
        want = urlsplit(req["url"])
        hits = []

        def on_response(resp):
            try:
                u = urlsplit(resp.url)
                if u.netloc == want.netloc and u.path == want.path and resp.request.method == (req.get("method") or "GET") \
                        and resp.status == 200:
                    hits.append(resp)
            except Exception:  # noqa: BLE001
                pass

        with _lock, self._sync() as p:
            ctx = self._launch(p, headless=headless, offscreen=not headless)
            ctx.on("response", on_response)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                try:
                    page.goto(start_url, wait_until="domcontentloaded", timeout=timeout_ms)
                except Exception as e:  # noqa: BLE001
                    raise E.HubError(E.NETWORK, "Встроенный браузер не смог открыть сайт", detail=str(e))
                end = time.time() + timeout_ms / 1000
                while not hits and time.time() < end:
                    page.wait_for_timeout(300)
                if not hits:
                    return None
                page.wait_for_timeout(500)
                best = next((h for h in hits if urlsplit(h.url).query == want.query), hits[-1])
                text = best.text()
                if is_check(200, text, page):
                    raise E.HubError(E.BOT, CHECK_MESSAGE)
                return text
            finally:
                try:
                    ctx.close()
                except Exception:  # noqa: BLE001
                    pass

    @staticmethod
    def _xhr(page, req, timeout_ms):
        for _attempt in range(3):
            frame = _frame_for(page, req)
            try:
                r = frame.evaluate(XHR_JS, {
                    "method": req.get("method") or "GET", "url": req["url"], "body": req.get("body") or "",
                    "timeout": timeout_ms,
                    "headers": {**(req.get("headers") or {}),
                                **({"Content-Type": req["content_type"]}
                                   if req.get("body") and req.get("content_type") else {})}})
                return r["status"], r["text"]
            except Exception:  # noqa: BLE001 — страница перезагрузилась (защита сайта) — ещё раз
                _settle(page)
        raise E.HubError(E.NETWORK, "Сайт перезагружал страницу и не дал выполнить запрос")

    @staticmethod
    def _human_check(ctx, page, req, start_url, second=False, human=None, wait_s=600):
        """Вкладка на экране: человек проходит проверку сам. Сначала — сам запрос (если проверка стоит перед
        данными), во второй раз — страница с формой (если проверку показывает форма после поиска)."""
        _show_window(page)
        tab = ctx.new_page()
        get = (req.get("method") or "GET").upper() == "GET"
        target = req["url"] if get and not second else (req.get("frame_url") or start_url)
        banner = CHECK_BANNER_FORM_JS if second or not get else CHECK_BANNER_JS
        try:
            tab.goto(target, wait_until="domcontentloaded", timeout=90000)
        except Exception:  # noqa: BLE001
            pass
        if human:
            human(tab)                          # тесты: «человек сдвинул ползунок»
        end, clean = time.time() + wait_s, 0
        while time.time() < end and not tab.is_closed():
            _in_frames(tab, banner)
            html = _frames_html(tab)
            clean = clean + 1 if html and not has_check_markers(html) else 0
            if clean >= 3 and not second:
                break                           # проверка пройдена — дальше хаб сам
            time.sleep(1)
        try:
            if not tab.is_closed():
                tab.close()
        except Exception:  # noqa: BLE001
            pass
        _settle(page)

    def pick(self, url, timeout_s=600, headless=False, on_page=None):
        """Окно выбора элемента. Возвращает словарь с выбором или None, если окно закрыли.

        Подсветка ставится во все рамки страницы (формы и таблицы часто внутри <iframe>), а после
        перезагрузки страницы (защита сайта, переход по ссылке) — заново."""
        with _lock, self._sync() as p:
            ctx = self._launch(p, headless=headless)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                if not headless:
                    _show_window(page)
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                _settle(page)
                _in_frames(page, PICKER_JS)
                if on_page:
                    on_page(page)      # для тестов: «щёлкнуть» программно
                end = time.time() + timeout_s
                while time.time() < end:
                    if page.is_closed():
                        return None
                    _in_frames(page, PICKER_JS)      # после перехода или перезагрузки подсветку ставим заново
                    for fr in list(page.frames):
                        try:
                            res = fr.evaluate("window.__hubPick")
                        except Exception:  # noqa: BLE001 — рамка перезагружается
                            res = None
                        if res:
                            time.sleep(0.8)
                            try:
                                res["page_html"] = fr.content()
                            except Exception:  # noqa: BLE001
                                res["page_html"] = page.content()
                            return res
                    time.sleep(0.3)
                return None
            finally:
                try:
                    ctx.close()
                except Exception:  # noqa: BLE001
                    pass


def _settle(page, timeout_ms=15000):
    """Дождаться, пока страница успокоится: сайты с защитой перезагружают её сразу после открытия."""
    try:
        page.wait_for_load_state("networkidle", timeout=timeout_ms)
    except Exception:  # noqa: BLE001
        pass


def _in_frames(page, js):
    """Выполнить скрипт во всех рамках страницы; рамка, которая как раз перезагружается, пропускается."""
    for fr in list(page.frames):
        try:
            fr.evaluate(js)
        except Exception:  # noqa: BLE001 — «Execution context was destroyed»: страница перезагружается
            pass


def _block_category(status, text):
    from ._web import detect_block
    return detect_block(status, text) if status else None


def _origin(url):
    from urllib.parse import urlsplit
    u = urlsplit(url or "")
    return f"{u.scheme}://{u.netloc}"


def _frame_for(page, req):
    """Рамка, из которой форма отправляла запрос (та же страница или хотя бы тот же сайт)."""
    want = (req.get("frame_url") or "").split("#")[0]
    frames = list(page.frames)
    for fr in frames:
        if want and fr.url.split("#")[0] == want:
            return fr
    for fr in frames:
        if want and fr.url.split("?")[0] == want.split("?")[0]:
            return fr
    origin = _origin(req["url"])
    for fr in frames:
        if _origin(fr.url) == origin:
            return fr
    page.goto(req.get("frame_url") or origin + "/", wait_until="domcontentloaded", timeout=90000)
    _settle(page)
    return page.main_frame


def has_check_markers(html) -> bool:
    low = (html or "")[:300000].lower()
    if any(m in low for m in CHECK_MARKERS):
        return True
    if any(m in low for m in GENERIC_MARKERS):
        visible = re.sub(r"<script.*?</script>|<style.*?</style>|<!--.*?-->", "", low, flags=re.S)
        return len(visible) < SMALL_PAGE
    return False


def is_check(status, text, page=None) -> bool:
    """Ответ — проверка «я не робот» (ползунок), а не данные: признаки проверки и нет таблицы с числами.
    Проверку может показать и сама страница с формой — тогда смотрим и на неё."""
    if status in (200, 403, 405, 429) and has_check_markers(text):
        from ._forms import result_table
        if result_table(text) is None:
            return True
    if status == 200 and page is not None:
        from ._forms import result_table
        if result_table(text) is None and has_check_markers(_frames_html(page)):
            return True
    return False


def _frames_html(page) -> str:
    parts = []
    for fr in list(page.frames):
        try:
            parts.append(fr.content())
        except Exception:  # noqa: BLE001
            pass
    return "\n".join(parts)


def _show_window(page):
    """Окно за краем экрана — на экран, поверх остальных (чтобы человек увидел проверку)."""
    try:
        cdp = page.context.new_cdp_session(page)
        wid = cdp.send("Browser.getWindowForTarget")["windowId"]
        cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"windowState": "normal"}})
        cdp.send("Browser.setWindowBounds", {"windowId": wid, "bounds": {"left": 80, "top": 60, "width": 1280,
                                                                          "height": 900}})
        page.bring_to_front()
    except Exception:  # noqa: BLE001 — без окна (тесты) или старый Chromium
        pass
