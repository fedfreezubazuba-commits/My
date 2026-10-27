"""
Шаблоны Power Query (M) лежат в hub/excel/templates/*.m — их можно читать глазами.
Здесь: подстановка пути к посылке и простая синтаксическая проверка (скобки, кавычки,
let/in), чтобы опечатку в шаблоне ловили тесты, а не Excel пользователя.
"""
from pathlib import Path

TEMPLATES = Path(__file__).resolve().parent / "templates"
# имя запроса -> файл шаблона; порядок важен: параметр создаётся первым
QUERIES = [("Хаб_Посылка", "Хаб_Посылка.m"), ("Хаб", "Хаб.m"), ("Хаб_Ряды", "Хаб_Ряды.m"),
           ("Хаб_Обновления", "Хаб_Обновления.m")]
TABLES = ["Хаб", "Хаб_Ряды", "Хаб_Обновления"]


def m_string(text: str) -> str:
    """Экранирование для строкового литерала M: кавычки удваиваются."""
    return str(text).replace('"', '""')


def render(template_file: str, packet_path) -> str:
    text = (TEMPLATES / template_file).read_text(encoding="utf-8")
    return text.replace("{{PACKET_PATH}}", m_string(str(packet_path)))


# Запасные латинские имена запросов: в некоторых сборках Excel подключение к запросу с русским
# именем отвергается («неверный аргумент»). Таблицы на листе всё равно называются Хаб и Хаб_Ряды.
ASCII_NAMES = {"Хаб_Посылка": "Hub_Packet", "Хаб": "Hub", "Хаб_Ряды": "Hub_Series", "Хаб_Обновления": "Hub_Updates"}


def query_names(ascii_names=False, sheets=()) -> dict:
    """{русское имя: имя запроса в книге} (с запросами листов файлов модели)."""
    out = dict(ASCII_NAMES) if ascii_names else {k: k for k in ASCII_NAMES}
    for q in sheet_queries(sheets, ascii_names):
        out[q["ru"]] = q["query"]
    return out


def sheet_queries(sheets, ascii_names=False) -> list[dict]:
    """Запросы листов файлов («Листы файла в модель»): для каждого листа — запрос самого листа и, если нужен,
    запрос «ячеек». [{ru, query, table, sheet, template, suffix}]. sheets — [{name, cells}]."""
    from ..core.config import slug
    from ..core.sheets import table_safe
    out, used = [], set()
    for sh in sheets or []:
        name = sh["name"]
        base = slug(name, 24) or "list"
        k, a = 2, base
        while a in used:
            a, k = f"{base}_{k}", k + 1
        used.add(a)
        t = table_safe(name)
        kinds = [("Лист", "Sheet", "Хаб_Лист.m", f" — лист {name}.csv", f"Лист_{t}", name)]
        if sh.get("cells", True):
            kinds.append(("Ячейки", "Cells", "Хаб_Ячейки.m", f" — лист {name} — ячейки.csv", f"Ячейки_{t}",
                          f"{name} (ячейки)"))
        for ru, en, tpl, suffix, table, ws in kinds:
            q_ru = f"Хаб_{ru}_{t}"
            out.append({"ru": q_ru, "query": f"Hub_{en}_{a}" if ascii_names else q_ru, "table": table,
                        "sheet": ws, "template": tpl, "suffix": suffix})
    return out


# вид таблицы «Хаб»: long — строка на значение (по умолчанию), wide — ряд на столбец
HUB_TEMPLATE = {"long": "Хаб_строки.m", "wide": "Хаб.m"}


def queries(packet_path, ascii_names=False, layout="long", sheets=()) -> list[tuple[str, str]]:
    """[(имя запроса, M-код)] для подключения модели (с листами файлов, если модель их получает)."""
    names = query_names(ascii_names)
    out = []
    for name, f in QUERIES:
        if name == "Хаб":
            f = HUB_TEMPLATE.get(layout, HUB_TEMPLATE["long"])
        code = render(f, packet_path)
        if ascii_names:
            code = code.replace("Хаб_Посылка", names["Хаб_Посылка"])
        out.append((names[name], code))
    for q in sheet_queries(sheets, ascii_names):
        code = (TEMPLATES / q["template"]).read_text(encoding="utf-8").replace("{{SUFFIX}}", m_string(q["suffix"]))
        if ascii_names:
            code = code.replace("Хаб_Посылка", names["Хаб_Посылка"])
        out.append((q["query"], code))
    return out


def strip_comments_and_strings(code: str) -> str:
    """Убирает комментарии и содержимое строк (для проверки скобок и поиска слов)."""
    out, i, n = [], 0, len(code)
    while i < n:
        c = code[i]
        if code.startswith("//", i):
            j = code.find("\n", i)
            i = n if j < 0 else j
            continue
        if code.startswith("/*", i):
            j = code.find("*/", i + 2)
            if j < 0:
                raise SyntaxError("Незакрытый комментарий /*")
            i = j + 2
            continue
        if c == '"':
            j = i + 1
            while True:
                j = code.find('"', j)
                if j < 0:
                    raise SyntaxError("Незакрытая строка")
                if code.startswith('""', j):
                    j += 2
                    continue
                break
            out.append('""')
            i = j + 1
            continue
        if code.startswith('#"', i):
            j = code.find('"', i + 2)
            if j < 0:
                raise SyntaxError('Незакрытый идентификатор #"')
            out.append("ID")
            i = j + 1
            continue
        out.append(c)
        i += 1
    return "".join(out)


def check_syntax(code: str) -> list[str]:
    """Простые проверки M-кода. Пустой список — замечаний нет."""
    problems = []
    try:
        bare = strip_comments_and_strings(code)
    except SyntaxError as e:
        return [str(e)]
    pairs = {")": "(", "]": "[", "}": "{"}
    stack = []
    for pos, c in enumerate(bare):
        if c in "([{":
            stack.append((c, pos))
        elif c in ")]}":
            if not stack or stack[-1][0] != pairs[c]:
                problems.append(f"Лишняя или несогласованная скобка «{c}» (позиция {pos})")
                break
            stack.pop()
    if stack:
        problems.append(f"Незакрытая скобка «{stack[-1][0]}»")
    import re
    words = re.findall(r"\b(let|in)\b", bare)
    if words.count("let") != words.count("in"):
        problems.append(f"Не совпадает число let ({words.count('let')}) и in ({words.count('in')})")
    if re.search(r",\s*in\b", bare):
        problems.append("Запятая перед in")
    return problems
