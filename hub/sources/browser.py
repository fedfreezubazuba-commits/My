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
  tip.textContent = 'Хаб данных: щёлкните по нужному числу. Удерживайте Shift и щёлкните — чтобы выбрать всю таблицу.';
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
      while (s) { const t = (s.innerText || '').trim(); if (t && t.length < 80 && !/\d{3}/.test(t)) return t; s = s.previousElementSibling; }
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
    window.__hubPick = {
      kind: e.shiftKey ? 'table' : 'value',
      selector: cssPath(el), xpath: xPath(el), text: (el.innerText || '').trim().slice(0, 500),
      label: labelBefore(el), html: e.shiftKey ? el.outerHTML.slice(0, 300000) : '', url: location.href
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
  b.textContent = 'Хаб данных: войдите на сайт (или выберите магазин) как обычно, затем просто закройте это окно.';
  document.documentElement.appendChild(b);
})();
"""


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

    def _launch(self, p, headless):
        if self.profile is None:
            raise E.HubError(E.INTERNAL, "Не задана рабочая папка для профиля браузера")
        self.profile.mkdir(parents=True, exist_ok=True)
        kw = {"headless": headless, "locale": "ru-RU", "viewport": {"width": 1280, "height": 900}}
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

    def get_html(self, url, wait_ms=2500, timeout_ms=60000):
        with _lock, self._sync() as p:
            ctx = self._launch(p, headless=True)
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
                return page.content(), page.url
            finally:
                ctx.close()

    def login(self, url, timeout_s=900):
        """Окно для входа. Возвращается, когда пользователь закрыл окно (или через 15 минут)."""
        with _lock, self._sync() as p:
            ctx = self._launch(p, headless=False)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                end = time.time() + timeout_s
                while time.time() < end and any(not pg.is_closed() for pg in ctx.pages):
                    for pg in ctx.pages:
                        try:
                            pg.evaluate(LOGIN_BANNER_JS)
                        except Exception:  # noqa: BLE001 — страница грузится или закрыта
                            pass
                    time.sleep(1)
            finally:
                try:
                    ctx.close()
                except Exception:  # noqa: BLE001
                    pass

    def pick(self, url, timeout_s=600, headless=False, on_page=None):
        """Окно выбора элемента. Возвращает словарь с выбором или None, если окно закрыли."""
        with _lock, self._sync() as p:
            ctx = self._launch(p, headless=headless)
            try:
                page = ctx.pages[0] if ctx.pages else ctx.new_page()
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.evaluate(PICKER_JS)
                if on_page:
                    on_page(page)      # для тестов: «щёлкнуть» программно
                end = time.time() + timeout_s
                while time.time() < end:
                    if page.is_closed():
                        return None
                    try:
                        page.evaluate(PICKER_JS)      # после перехода по ссылке подсветку ставим заново
                        res = page.evaluate("window.__hubPick")
                    except Exception:  # noqa: BLE001 — страница перезагружается
                        res = None
                    if res:
                        time.sleep(0.8)
                        res["page_html"] = page.content()
                        return res
                    time.sleep(0.3)
                return None
            finally:
                try:
                    ctx.close()
                except Exception:  # noqa: BLE001
                    pass
