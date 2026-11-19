"""Извлечение текста из файлов для полнотекстового поиска (режим «Глубокое сканирование»)."""
from __future__ import annotations

import codecs
import html
import logging
import re
import zipfile
from typing import Optional

from settings import Settings

log = logging.getLogger("deepsearch.content")
logging.getLogger("pypdf").setLevel(logging.ERROR)

_TAGS = re.compile(r"<[^>]+>")
_PARA_END = re.compile(r"</(?:w:p|a:p|text:p|si|row|c)>|<w:br\s*/>|<w:tab\s*/>|<text:line-break\s*/>")
_WS = re.compile(r"[ \t\r\f\v]+")
_OOXML_DOC = {"docx", "docm", "dotx", "dotm"}
_OOXML_XLS = {"xlsx", "xlsm", "xltx"}
_OOXML_PPT = {"pptx", "pptm", "potx", "ppsx"}
_ODF = {"odt", "ods", "odp", "odg"}


def decode_bytes(data: bytes) -> str:
    """Декодирует текст: UTF-8/16 с BOM, затем UTF-8, затем cp1251."""
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    if data.startswith(b"\xef\xbb\xbf"):
        data = data[3:]
    try:
        return codecs.getincrementaldecoder("utf-8")().decode(data, final=False)
    except UnicodeDecodeError:
        return data.decode("cp1251", errors="replace")


def _xml_text(raw: bytes) -> str:
    s = raw.decode("utf-8", errors="replace")
    s = _PARA_END.sub("\n", s)
    s = _TAGS.sub("", s)
    s = html.unescape(s)
    return _WS.sub(" ", s)


def _zip_text(path: str, ext: str, limit: int) -> Optional[str]:
    out: list[str] = []
    total = 0
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        s = set(names)
        if "word/document.xml" in s:
            parts = ["word/document.xml"]
        elif "xl/sharedStrings.xml" in s:
            parts = ["xl/sharedStrings.xml"]
        elif "ppt/presentation.xml" in s:
            parts = sorted((n for n in names if n.startswith("ppt/slides/slide") and n.endswith(".xml")),
                           key=lambda n: (len(n), n))
        elif "content.xml" in s:
            parts = ["content.xml"]
        else:
            return None
        for part in parts:
            with z.open(part) as fh:
                raw = fh.read(limit * 12)      # защита от «бомб»: читаем ограниченный кусок
            txt = _xml_text(raw)
            out.append(txt)
            total += len(txt)
            if total >= limit:
                break
    return "\n".join(out)[:limit]


def _pdf_text(path: str, limit: int) -> Optional[str]:
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    reader = PdfReader(path)
    if reader.is_encrypted:
        return None
    chunks, total = [], 0
    for page in reader.pages[:80]:
        try:
            t = page.extract_text() or ""
        except Exception:
            continue
        chunks.append(t)
        total += len(t)
        if total >= limit:
            break
    return "\n".join(chunks)[:limit] or None


_CP1251_RUN = re.compile(r"[A-Za-zА-Яа-яЁё0-9 ,.;:!?()\-\"'«»—–/@%№_]{6,}")


def _ole_strings(path: str, limit: int) -> Optional[str]:
    """Эвристика для .doc/.xls/.ppt: извлекаются читаемые строки (не полноценный разбор формата)."""
    with open(path, "rb") as fh:
        data = fh.read(6 * 1024 * 1024)
    found: list[str] = []
    for off in (0, 1):
        txt = data[off:len(data) - ((len(data) - off) % 2)].decode("utf-16-le", errors="ignore")
        found += [m for m in re.findall(r"[\w ,.;:!?()\-\"'«»—–]{8,}", txt) if not m.isdigit()]
    found += _CP1251_RUN.findall(data.decode("cp1251", errors="ignore"))
    return "\n".join(found)[:limit] or None


def _rtf_text(raw: bytes, limit: int) -> str:
    s = raw.decode("latin-1")
    s = re.sub(r"\\'([0-9a-fA-F]{2})", lambda m: bytes.fromhex(m.group(1)).decode("cp1251", "replace"), s)
    s = re.sub(r"\\u(-?\d+)\??", lambda m: chr(int(m.group(1)) % 65536), s)
    s = re.sub(r"\\[a-zA-Z]+-?\d* ?|[{}]", " ", s)
    return _WS.sub(" ", s)[:limit]


def extract_text(path: str, ext: str, size: int, sig_name: str, st: Settings) -> Optional[str]:
    """Возвращает текст файла (обрезанный до лимита) или None, если формат не поддерживается."""
    limit = max(1, st.content_max_kb) * 1024
    max_container = st.content_max_file_mb * 1024 * 1024
    if size == 0:
        return None
    try:
        if ext in _OOXML_DOC | _OOXML_XLS | _OOXML_PPT | _ODF or sig_name in ("DOCX", "XLSX", "PPTX", "ODF"):
            return _zip_text(path, ext, limit) if size <= max_container else None
        if ext == "pdf" or sig_name == "PDF":
            return _pdf_text(path, limit) if size <= max_container else None
        if ext in ("doc", "xls", "ppt") or (sig_name.startswith("OLE") and ext in ("", "dot", "xlt", "pps", "pot")):
            return _ole_strings(path, limit)
        if ext == "rtf" or sig_name == "RTF":
            with open(path, "rb") as fh:
                return _rtf_text(fh.read(limit * 4), limit)
        if ext in st.text_exts or sig_name in ("Текст", "XML", "HTML", "Скрипт", "SVG"):
            with open(path, "rb") as fh:
                return decode_bytes(fh.read(limit))
    except Exception as exc:
        log.debug("Не удалось извлечь текст из %s: %s", path, exc)
    return None
