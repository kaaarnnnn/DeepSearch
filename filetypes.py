"""Классификация файлов: по расширению и по сигнатуре (магическим байтам)."""
from __future__ import annotations

import codecs
import zipfile
from dataclasses import dataclass
from typing import Optional

CAT_LABELS = {
    "documents": "Документ",
    "images": "Изображение",
    "video": "Видео",
    "audio": "Аудио",
    "archives": "Архив",
    "apps": "Приложение",
    "code": "Исходный код",
    "system": "Системный",
    "temp": "Временный/резервный",
    "other": "Прочее",
}
CAT_LABELS_PLURAL = {
    "documents": "Документы", "images": "Изображения", "video": "Видео", "audio": "Аудио",
    "archives": "Архивы", "apps": "Приложения", "code": "Исходный код", "system": "Системные",
    "temp": "Временные и резервные", "other": "Прочее",
}
CAT_COLORS = {
    "documents": "#4C8DFF", "images": "#2BC48A", "video": "#FF6B6B", "audio": "#B36BFF",
    "archives": "#FFB020", "apps": "#26C6DA", "code": "#8BC34A", "system": "#8A93A5",
    "temp": "#C98B5E", "other": "#6B7385",
}

# Флаги файла (битовая маска в БД)
F_HIDDEN, F_SYSTEM, F_READONLY, F_TEMP, F_NOEXT, F_LINK, F_CLOUD = 1, 2, 4, 8, 16, 32, 64

_EXT_GROUPS = {
    "documents": "pdf doc docx docm dot dotx dotm rtf txt odt ods odp ott xls xlsx xlsm xlsb xlt xltx csv tsv ppt "
                 "pptx pptm pps ppsx pot potx md markdown epub djvu fb2 mobi tex xps pages numbers key one vsd vsdx "
                 "msg eml wps pub mpp odg oxps",
    "images": "png jpg jpeg jpe jfif webp svg svgz bmp gif ico tif tiff heic heif avif raw cr2 nef arw dng orf rw2 "
              "psd ai eps xcf cur dds tga exr hdr",
    "video": "mp4 avi mkv mov wmv flv webm m4v mpg mpeg mpe 3gp 3g2 vob m2ts mts ogv divx f4v rm rmvb asf swf",
    "audio": "mp3 wav flac aac ogg oga m4a m4b wma opus aiff aif aifc mid midi amr ape wv mka ac3 dts",
    "archives": "zip rar 7z tar gz tgz bz2 tbz2 xz txz zst cab iso img lz lzma z cpio arj lzh wim dmg vhd vhdx vmdk",
    "apps": "exe msi bat cmd com scr ps1 vbs vbe wsf msix appx msp apk jar lnk appref-ms gadget",
    "code": "py pyw pyi pyc cpp cc cxx c h hpp hxx js mjs cjs ts tsx jsx html htm xhtml css scss sass less java kt "
            "kts cs go rs rb php swift lua sql sh bash zsh json xml yml yaml toml ini cfg conf vue dart r pl pm m "
            "mm asm s vb vbs-src gradle cmake makefile sln csproj vcxproj ipynb tf proto graphql lock",
    "system": "dll sys drv ocx cpl efi mui inf cat etl mum ime ax tlb manifest msc nls sdb dat-sys pnf",
}
EXT_CATEGORY: dict[str, str] = {}
for _cat, _exts in _EXT_GROUPS.items():
    for _e in _exts.split():
        EXT_CATEGORY.setdefault(_e, _cat)

TEMP_EXTS = frozenset("tmp temp bak old orig swp swo crdownload part partial dmp chk gid ~ bk bkp backup "
                      "dmp wbk asd sav".split())


def category_for(ext: str) -> str:
    if ext in TEMP_EXTS:
        return "temp"
    return EXT_CATEGORY.get(ext, "other")


def is_temp_name(name: str, ext: str) -> bool:
    return ext in TEMP_EXTS or name.startswith("~$") or name.endswith("~")


# ---------------------------------------------------------------------------
# Сигнатуры
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Sig:
    name: str
    category: str
    exts: Optional[frozenset]   # допустимые расширения; None — не проверять несоответствие


def _s(name, cat, exts):
    return Sig(name, cat, frozenset(exts.split()) if exts else None)


_ZIP_EXTS = ("zip docx docm dotx dotm xlsx xlsm xltx xlsb pptx pptm potx ppsx odt ods odp odg epub jar war ear apk "
             "aar xpi xps oxps vsdx nupkg whl msix appx 3mf kmz crx mcpack sketch")
_PE_EXTS = "exe dll sys ocx scr cpl drv efi mui ax tlb com winmd node acm ime rll tsp mun"
_OLE_EXTS = "doc xls ppt msi msg vsd pub dot xlt pps mpp xla ppa wps pot msp mst hwp db suo sdf"

_PREFIX_SIGS = [
    (b"\x89PNG\r\n\x1a\n", _s("PNG", "images", "png")),
    (b"\xff\xd8\xff", _s("JPEG", "images", "jpg jpeg jpe jfif")),
    (b"GIF87a", _s("GIF", "images", "gif")),
    (b"GIF89a", _s("GIF", "images", "gif")),
    (b"II*\x00", _s("TIFF", "images", "tif tiff cr2 nef dng arw orf")),
    (b"MM\x00*", _s("TIFF", "images", "tif tiff nef dng")),
    (b"\x00\x00\x01\x00", _s("ICO", "images", "ico")),
    (b"8BPS", _s("PSD", "images", "psd")),
    (b"%PDF-", _s("PDF", "documents", "pdf ai")),
    (b"PK\x03\x04", _s("ZIP", "archives", _ZIP_EXTS)),
    (b"PK\x05\x06", _s("ZIP", "archives", _ZIP_EXTS)),
    (b"Rar!\x1a\x07", _s("RAR", "archives", "rar")),
    (b"7z\xbc\xaf\x27\x1c", _s("7Z", "archives", "7z")),
    (b"\x1f\x8b", _s("GZIP", "archives", "gz tgz svgz")),
    (b"\xfd7zXZ\x00", _s("XZ", "archives", "xz txz")),
    (b"(\xb5/\xfd", _s("ZSTD", "archives", "zst")),
    (b"MSCF", _s("CAB", "archives", "cab")),
    (b"MZ", _s("PE (EXE/DLL)", "apps", _PE_EXTS)),
    (b"\x7fELF", _s("ELF", "apps", "so elf bin o")),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", _s("OLE (Office)", "documents", _OLE_EXTS)),
    (b"SQLite format 3\x00", _s("SQLite", "other", "db sqlite sqlite3 db3 sdb dat")),
    (b"{\\rtf", _s("RTF", "documents", "rtf")),
    (b"%!PS", _s("PostScript", "documents", "ps eps ai")),
    (b"ID3", _s("MP3", "audio", "mp3")),
    (b"fLaC", _s("FLAC", "audio", "flac")),
    (b"OggS", _s("OGG", "audio", "ogg oga ogv opus spx")),
    (b"\x1aE\xdf\xa3", _s("Matroska/WebM", "video", "mkv webm mka")),
    (b"\x30\x26\xb2\x75\x8e\x66\xcf\x11", _s("ASF (WMV/WMA)", "video", "wmv wma asf")),
    (b"FLV\x01", _s("FLV", "video", "flv f4v")),
    (b"MThd", _s("MIDI", "audio", "mid midi")),
    (b"\x00\x00\x01\xba", _s("MPEG-PS", "video", "mpg mpeg vob")),
    (b"\x00\x00\x01\xb3", _s("MPEG", "video", "mpg mpeg m2v")),
    (b"wOFF", _s("WOFF", "other", "woff")),
    (b"wOF2", _s("WOFF2", "other", "woff2")),
    (b"ITSF", _s("CHM", "documents", "chm")),
]

_TEXT_SIG = _s("Текст", "documents", "")
_XML_SIG = _s("XML", "code", "")
_HTML_SIG = _s("HTML", "code", "")
_SVG_SIG = _s("SVG", "images", "svg")
_SCRIPT_SIG = _s("Скрипт", "code", "")


def _looks_like_text(head: bytes) -> bool:
    if not head:
        return False
    if head.startswith((b"\xff\xfe", b"\xfe\xff", b"\xef\xbb\xbf")):
        return True
    if b"\x00" in head:
        return False
    try:
        codecs.getincrementaldecoder("utf-8")().decode(head, final=False)
        return True
    except UnicodeDecodeError:
        pass
    printable = sum(1 for b in head if b >= 0x20 or b in (9, 10, 13))
    return printable / len(head) > 0.95


def detect_signature(head: bytes, ext: str = "") -> Optional[Sig]:
    """Определяет тип по первым байтам файла. None — тип не распознан."""
    if not head:
        return None
    for magic, sig in _PREFIX_SIGS:
        if head.startswith(magic):
            return sig
    n = len(head)
    if n >= 14 and head[:2] == b"BM" and head[6:10] == b"\x00\x00\x00\x00":
        return _s("BMP", "images", "bmp dib")
    if n >= 12 and head[:4] == b"RIFF":
        kind = head[8:12]
        if kind == b"WEBP":
            return _s("WEBP", "images", "webp")
        if kind == b"WAVE":
            return _s("WAV", "audio", "wav wave")
        if kind == b"AVI ":
            return _s("AVI", "video", "avi")
        return _s("RIFF", "other", "")
    if n >= 12 and head[4:8] == b"ftyp":
        brand = head[8:12]
        if brand == b"qt  ":
            return _s("MOV", "video", "mov qt")
        if brand in (b"M4A ", b"M4B "):
            return _s("M4A", "audio", "m4a m4b mp4 aac")
        if brand in (b"heic", b"heix", b"hevc", b"heim", b"mif1", b"msf1", b"avif"):
            return _s("HEIF/AVIF", "images", "heic heif avif hif")
        if brand.startswith(b"3g"):
            return _s("3GP", "video", "3gp 3g2 mp4")
        return _s("MP4", "video", "mp4 m4v m4a mov f4v mp4v 3gp m4b")
    if n >= 262 and head[257:262] == b"ustar":
        return _s("TAR", "archives", "tar")
    if n >= 2 and head[0] == 0xFF and ext in ("", "mp3", "mp2", "aac", "adts", "mpga"):
        if (head[1] & 0xF6) == 0xF0:
            return _s("AAC", "audio", "aac adts")
        if (head[1] & 0xE0) == 0xE0:
            return _s("MP3", "audio", "mp3 mp2 mpga")
    if head.startswith(b"BZh") and n > 3 and 0x31 <= head[3] <= 0x39:
        return _s("BZIP2", "archives", "bz2 tbz2")
    # Текстовые форматы
    stripped = head.lstrip(b"\xef\xbb\xbf \t\r\n")[:512]
    low = stripped.lower()
    if low.startswith(b"<?xml"):
        return _SVG_SIG if b"<svg" in head[:2048].lower() else _XML_SIG
    if low.startswith(b"<svg"):
        return _SVG_SIG
    if low.startswith((b"<!doctype html", b"<html")):
        return _HTML_SIG
    if head.startswith(b"#!"):
        return _SCRIPT_SIG
    if _looks_like_text(head):
        return _TEXT_SIG
    return None


def refine_zip(path: str, fallback: Sig) -> Sig:
    """Уточняет ZIP: DOCX/XLSX/PPTX/ODF/EPUB/JAR/APK — по содержимому центрального каталога."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if len(names) > 20000:
                names = names[:20000]
            s = set(names)
            if "word/document.xml" in s:
                return _s("DOCX", "documents", "docx docm dotx dotm")
            if "xl/workbook.xml" in s:
                return _s("XLSX", "documents", "xlsx xlsm xltx xlsb")
            if "ppt/presentation.xml" in s:
                return _s("PPTX", "documents", "pptx pptm potx ppsx")
            if "mimetype" in s:
                mt = z.read("mimetype")[:100].decode("ascii", "ignore")
                if "opendocument" in mt:
                    return _s("ODF", "documents", "odt ods odp odg")
                if "epub" in mt:
                    return _s("EPUB", "documents", "epub")
            if "AndroidManifest.xml" in s:
                return _s("APK", "apps", "apk")
            if "META-INF/MANIFEST.MF" in s:
                return _s("JAR", "apps", "jar war ear")
    except Exception:
        pass
    return fallback


def ext_matches(sig: Sig, ext: str) -> bool:
    """True, если расширение соответствует найденной сигнатуре (или проверка неприменима)."""
    return sig.exts is None or not ext or ext in sig.exts
