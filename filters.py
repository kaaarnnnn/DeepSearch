"""Фильтры поиска: модель условий, разбор размеров и дат, построение SQL."""
from __future__ import annotations

import re
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Optional

from filetypes import F_CLOUD, F_HIDDEN, F_LINK, F_NOEXT, F_SYSTEM, F_TEMP

CATEGORY_TYPES = {"documents", "images", "video", "audio", "archives", "apps", "code", "other"}
SPECIAL_TYPES = {"system", "temp", "noext", "hidden", "link", "cloud"}
ALL_TYPES = CATEGORY_TYPES | SPECIAL_TYPES

TYPE_ALIASES = {
    "doc": "documents", "docs": "documents", "document": "documents", "documents": "documents",
    "документ": "documents", "документы": "documents",
    "image": "images", "images": "images", "img": "images", "picture": "images", "pictures": "images",
    "изображение": "images", "изображения": "images", "картинки": "images", "фото": "images",
    "video": "video", "videos": "video", "видео": "video",
    "audio": "audio", "music": "audio", "аудио": "audio", "музыка": "audio",
    "archive": "archives", "archives": "archives", "архив": "archives", "архивы": "archives",
    "app": "apps", "apps": "apps", "exe": "apps", "приложение": "apps", "приложения": "apps", "программы": "apps",
    "code": "code", "source": "code", "код": "code", "исходники": "code",
    "system": "system", "sys": "system", "системные": "system", "системный": "system",
    "temp": "temp", "tmp": "temp", "backup": "temp", "временные": "temp", "временный": "temp", "резервные": "temp",
    "noext": "noext", "без_расширения": "noext", "безрасширения": "noext",
    "hidden": "hidden", "скрытые": "hidden", "скрытый": "hidden",
    "link": "link", "ссылки": "link", "cloud": "cloud", "облако": "cloud",
    "other": "other", "прочее": "other",
}

_UNITS = {"": 1, "b": 1, "б": 1, "k": 1024, "kb": 1024, "кб": 1024, "m": 1024 ** 2, "mb": 1024 ** 2, "мб": 1024 ** 2,
          "g": 1024 ** 3, "gb": 1024 ** 3, "гб": 1024 ** 3, "t": 1024 ** 4, "tb": 1024 ** 4, "тб": 1024 ** 4}
_SIZE_RE = re.compile(r"^\s*(\d+(?:[.,]\d+)?)\s*([a-zа-я]*)\s*$", re.IGNORECASE)


class FilterError(ValueError):
    """Ошибка в синтаксисе условия поиска (текст пригоден для показа пользователю)."""


def parse_size(text: str) -> int:
    m = _SIZE_RE.match(text)
    if not m or m.group(2).lower() not in _UNITS:
        raise FilterError(f"Не удалось разобрать размер «{text}». Примеры: 10mb, 1.5гб, 500kb")
    return int(float(m.group(1).replace(",", ".")) * _UNITS[m.group(2).lower()])


def parse_size_range(expr: str) -> tuple[Optional[int], Optional[int]]:
    """'>10mb', '<=1gb', '1mb..5mb', '5mb' -> (min, max) включительно."""
    expr = expr.strip()
    if ".." in expr:
        a, b = expr.split("..", 1)
        return (parse_size(a) if a.strip() else None, parse_size(b) if b.strip() else None)
    for op in (">=", "<=", ">", "<", "="):
        if expr.startswith(op):
            v = parse_size(expr[len(op):])
            return {">=": (v, None), ">": (v + 1, None), "<=": (None, v), "<": (None, v - 1), "=": (v, v)}[op]
    v = parse_size(expr)
    return v, v


# ----------------------------------------------------------------------------- даты
_DATE_FORMATS = [
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), lambda m: (int(m[1]), int(m[2]), int(m[3]))),
    (re.compile(r"^(\d{1,2})\.(\d{1,2})\.(\d{4})$"), lambda m: (int(m[3]), int(m[2]), int(m[1]))),
]
_REL_RE = re.compile(r"^(\d+)([dwmyднмг])$", re.IGNORECASE)


def _period(text: str) -> tuple[datetime, datetime]:
    """Календарный период, заданный строкой: год, месяц или день -> [начало, конец)."""
    t = text.strip().lower()
    now = datetime.now()
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if t in ("today", "сегодня"):
        return today, today + timedelta(days=1)
    if t in ("yesterday", "вчера"):
        return today - timedelta(days=1), today
    if re.fullmatch(r"\d{4}", t):
        y = int(t)
        return datetime(y, 1, 1), datetime(y + 1, 1, 1)
    m = re.fullmatch(r"(\d{4})-(\d{1,2})", t)
    if m:
        y, mo = int(m[1]), int(m[2])
        if not 1 <= mo <= 12:
            raise FilterError(f"Неверный месяц в дате «{text}»")
        return datetime(y, mo, 1), datetime(y + (mo == 12), mo % 12 + 1, 1)
    for rx, conv in _DATE_FORMATS:
        m = rx.match(t)
        if m:
            try:
                d = datetime(*conv(m))
            except ValueError:
                raise FilterError(f"Неверная дата «{text}»") from None
            return d, d + timedelta(days=1)
    raise FilterError(f"Не удалось разобрать дату «{text}». Примеры: 2024-05-01, 01.05.2024, 2024-05, today, 7d")


def parse_date_range(expr: str) -> tuple[Optional[int], Optional[int]]:
    """Возвращает (с, до) в секундах Unix: нижняя граница включительно, верхняя — исключительно."""
    e = expr.strip().lower()
    now = datetime.now()
    rel = {"week": 7, "неделя": 7, "month": 30, "месяц": 30, "year": 365, "год": 365}
    if e in rel:
        return int((now - timedelta(days=rel[e])).timestamp()), None
    m = _REL_RE.match(e)
    if m:
        n, u = int(m[1]), m[2]
        days = n * {"d": 1, "д": 1, "н": 7, "w": 7, "m": 30, "м": 30, "y": 365, "г": 365}[u]
        return int((now - timedelta(days=days)).timestamp()), None
    if ".." in e:
        a, b = e.split("..", 1)
        lo = int(_period(a)[0].timestamp()) if a.strip() else None
        hi = int(_period(b)[1].timestamp()) if b.strip() else None
        return lo, hi
    for op in (">=", "<=", ">", "<", "="):
        if e.startswith(op):
            s, en = _period(e[len(op):])
            s, en = int(s.timestamp()), int(en.timestamp())
            return {">=": (s, None), ">": (en, None), "<=": (None, en), "<": (None, s), "=": (s, en)}[op]
    s, en = _period(e)
    return int(s.timestamp()), int(en.timestamp())


# ----------------------------------------------------------------------------- модель фильтра
def _like_escape(s: str) -> str:
    return s.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def wildcard_to_like(term: str) -> str:
    """Шаблон с * и ? -> LIKE-шаблон (сопоставление всего имени)."""
    out = []
    for ch in term:
        if ch == "*":
            out.append("%")
        elif ch == "?":
            out.append("_")
        else:
            out.append(_like_escape(ch))
    return "".join(out)


def fts_query(text: str) -> str:
    """Безопасный запрос FTS5: слова — префиксный поиск, «фразы в кавычках» — точная фраза."""
    parts = []
    for m in re.finditer(r'"([^"]+)"|(\S+)', text):
        if m.group(1):
            parts.append('"' + m.group(1).replace('"', '""') + '"')
        else:
            word = re.sub(r"[^\w]+", " ", m.group(2), flags=re.UNICODE).strip()
            for w in word.split():
                parts.append('"' + w.replace('"', '""') + '"*')
    return " ".join(parts)


@dataclass
class FileFilter:
    terms: list = field(default_factory=list)          # подстроки/шаблоны в имени (нижний регистр)
    not_terms: list = field(default_factory=list)
    exts: list = field(default_factory=list)           # '' = без расширения
    not_exts: list = field(default_factory=list)
    types: list = field(default_factory=list)
    not_types: list = field(default_factory=list)
    size_min: Optional[int] = None
    size_max: Optional[int] = None
    ctime_min: Optional[int] = None
    ctime_max: Optional[int] = None
    mtime_min: Optional[int] = None
    mtime_max: Optional[int] = None
    paths: list = field(default_factory=list)
    not_paths: list = field(default_factory=list)
    content: str = ""
    regex: str = ""
    flag_conds: dict = field(default_factory=dict)     # {маска: True/False}
    access_bad: Optional[bool] = None
    mismatch: Optional[bool] = None
    duplicates: bool = False

    def is_empty(self) -> bool:
        return self == FileFilter()

    def combined(self, other: "FileFilter") -> "FileFilter":
        """Объединяет два фильтра по правилу «И»."""
        r = replace(self)
        for name in ("terms", "not_terms", "exts", "not_exts", "types", "not_types", "paths", "not_paths"):
            setattr(r, name, list(getattr(self, name)) + list(getattr(other, name)))
        for lo, hi in (("size_min", "size_max"), ("ctime_min", "ctime_max"), ("mtime_min", "mtime_max")):
            a, b = getattr(self, lo), getattr(other, lo)
            setattr(r, lo, max([v for v in (a, b) if v is not None], default=None))
            a, b = getattr(self, hi), getattr(other, hi)
            setattr(r, hi, min([v for v in (a, b) if v is not None], default=None))
        r.content = " ".join(x for x in (self.content, other.content) if x)
        r.regex = other.regex or self.regex
        r.flag_conds = {**self.flag_conds, **other.flag_conds}
        r.access_bad = other.access_bad if other.access_bad is not None else self.access_bad
        r.mismatch = other.mismatch if other.mismatch is not None else self.mismatch
        r.duplicates = self.duplicates or other.duplicates
        return r

    # ------------------------------------------------------------------
    def where(self) -> tuple[str, list]:
        """SQL-условие для таблицы files с псевдонимом f."""
        c: list[str] = []
        p: list = []

        def name_cond(term: str, neg: bool):
            if "*" in term or "?" in term:
                cond, val = "f.name_lc LIKE ? ESCAPE '\\'", wildcard_to_like(term)
            else:
                cond, val = "instr(f.name_lc, ?) > 0", term
            c.append(f"NOT ({cond})" if neg else cond)
            p.append(val)

        for t in self.terms:
            name_cond(t, False)
        for t in self.not_terms:
            name_cond(t, True)
        if self.exts:
            c.append(f"f.ext IN ({','.join('?' * len(self.exts))})")
            p += self.exts
        if self.not_exts:
            c.append(f"f.ext NOT IN ({','.join('?' * len(self.not_exts))})")
            p += self.not_exts

        def type_cond(t: str) -> tuple[str, list]:
            if t in CATEGORY_TYPES:
                return "f.category = ?", [t]
            return {
                "system": (f"(f.category = 'system' OR f.flags & {F_SYSTEM})", []),
                "temp": (f"(f.flags & {F_TEMP})", []),
                "noext": (f"(f.flags & {F_NOEXT})", []),
                "hidden": (f"(f.flags & {F_HIDDEN})", []),
                "link": (f"(f.flags & {F_LINK})", []),
                "cloud": (f"(f.flags & {F_CLOUD})", []),
            }[t]

        if self.types:
            parts = [type_cond(t) for t in self.types]
            c.append("(" + " OR ".join(x[0] for x in parts) + ")")
            for x in parts:
                p += x[1]
        for t in self.not_types:
            cond, vals = type_cond(t)
            c.append(f"NOT {cond}")
            p += vals
        if self.size_min is not None:
            c.append("f.size >= ?")
            p.append(self.size_min)
        if self.size_max is not None:
            c.append("f.size <= ?")
            p.append(self.size_max)
        for col, lo, hi in (("ctime", self.ctime_min, self.ctime_max), ("mtime", self.mtime_min, self.mtime_max)):
            if lo is not None:
                c.append(f"f.{col} >= ?")
                p.append(lo)
            if hi is not None:
                c.append(f"f.{col} < ?")
                p.append(hi)
        for t in self.paths:
            c.append("instr(ulower(f.path), ?) > 0")
            p.append(t)
        for t in self.not_paths:
            c.append("NOT instr(ulower(f.path), ?) > 0")
            p.append(t)
        if self.regex:
            c.append("f.name REGEXP ?")
            p.append(self.regex)
        for mask, want in self.flag_conds.items():
            c.append(f"(f.flags & {mask}) {'<>' if want else '='} 0")
        if self.access_bad is not None:
            c.append("f.access NOT IN (1,3)" if self.access_bad else "f.access IN (1,3)")
        if self.mismatch is not None:
            c.append("f.mismatch = 1" if self.mismatch else "f.mismatch = 0")
        if self.content:
            q = fts_query(self.content)
            if q:
                c.append("f.id IN (SELECT rowid FROM content_fts WHERE content_fts MATCH ?)")
                p.append(q)
        return (" AND ".join(c) if c else "1"), p
