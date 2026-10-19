"""Форматирование чисел и дат для людей: 15,8; 1 979; 30.09.2025."""


def fmt(v) -> str:
    if v is None:
        return "—"
    if isinstance(v, float) and v.is_integer() and abs(v) < 1e15:
        v = int(v)
    if isinstance(v, int):
        return f"{v:,}".replace(",", " ")
    s = f"{v:,.6f}".rstrip("0").rstrip(".")
    return s.replace(",", " ").replace(".", ",")


def fmt_date(iso) -> str:
    if not iso:
        return "—"
    s = str(iso)
    if len(s) >= 10 and s[4] == "-":
        return f"{s[8:10]}.{s[5:7]}.{s[:4]}" + (f" {s[11:16]}" if len(s) > 11 else "")
    return s
