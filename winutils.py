"""Слой интеграции с Windows (с безопасными запасными вариантами для других ОС).

Здесь нет ничего, что обходит защиту системы: только перечисление дисков,
длинные пути, проверка прав администратора и стандартные действия оболочки.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass

IS_WIN = os.name == "nt"
SEP = os.sep

# Атрибуты файлов Windows
ATTR_READONLY = 0x1
ATTR_HIDDEN = 0x2
ATTR_SYSTEM = 0x4
ATTR_DIRECTORY = 0x10
ATTR_TEMPORARY = 0x100
ATTR_REPARSE = 0x400
ATTR_OFFLINE = 0x1000
ATTR_RECALL_ON_OPEN = 0x40000
ATTR_RECALL_ON_DATA_ACCESS = 0x400000
ATTR_CLOUD_MASK = ATTR_OFFLINE | ATTR_RECALL_ON_OPEN | ATTR_RECALL_ON_DATA_ACCESS


@dataclass
class DriveInfo:
    root: str
    label: str
    fs: str
    kind: str          # fixed / removable / network / cdrom / ramdisk / unknown
    total: int = 0
    free: int = 0
    ready: bool = True

    @property
    def used(self) -> int:
        return max(0, self.total - self.free)

    @property
    def title(self) -> str:
        names = {"fixed": "Локальный диск", "removable": "Съёмный диск", "network": "Сетевой диск",
                 "cdrom": "Оптический диск", "ramdisk": "RAM-диск", "unknown": "Диск"}
        return self.label or names.get(self.kind, "Диск")


_error_mode_set = False


def _silence_drive_errors() -> None:
    """Не показывать системные окна «Вставьте диск» при опросе пустых приводов."""
    global _error_mode_set
    if IS_WIN and not _error_mode_set:
        import ctypes
        ctypes.windll.kernel32.SetErrorMode(0x0001 | 0x8000)  # FAILCRITICALERRORS | NOOPENFILEERRORBOX
        _error_mode_set = True


def list_drives() -> list[DriveInfo]:
    """Список подключённых в данный момент дисков/разделов."""
    if not IS_WIN:
        try:
            du = shutil.disk_usage("/")
            return [DriveInfo("/", "Корень", "", "fixed", du.total, du.free)]
        except OSError:
            return [DriveInfo("/", "Корень", "", "fixed")]
    import ctypes
    _silence_drive_errors()
    k32 = ctypes.windll.kernel32
    mask = k32.GetLogicalDrives()
    kinds = {2: "removable", 3: "fixed", 4: "network", 5: "cdrom", 6: "ramdisk"}
    out: list[DriveInfo] = []
    for i in range(26):
        if not (mask >> i) & 1:
            continue
        root = f"{chr(65 + i)}:\\"
        kind = kinds.get(k32.GetDriveTypeW(root), "unknown")
        label = ctypes.create_unicode_buffer(261)
        fs = ctypes.create_unicode_buffer(261)
        ok = k32.GetVolumeInformationW(root, label, 261, None, None, None, fs, 261)
        total = free = 0
        if ok:
            try:
                du = shutil.disk_usage(root)
                total, free = du.total, du.free
            except OSError:
                ok = 0
        out.append(DriveInfo(root, label.value if ok else "", fs.value if ok else "", kind, total, free, bool(ok)))
    return out


def drive_roots_present() -> set[str]:
    return {d.root.upper() for d in list_drives() if d.ready}


def is_admin() -> bool:
    try:
        if IS_WIN:
            import ctypes
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        return os.geteuid() == 0
    except Exception:
        return False


def restart_as_admin() -> bool:
    """Перезапуск программы с запросом UAC. Возвращает True, если запрос отправлен."""
    if not IS_WIN:
        return False
    import ctypes
    if getattr(sys, "frozen", False):
        exe, params = sys.executable, " ".join(f'"{a}"' for a in sys.argv[1:])
    else:
        exe = sys.executable
        params = " ".join(f'"{a}"' for a in [os.path.abspath(sys.argv[0]), *sys.argv[1:]])
    rc = ctypes.windll.shell32.ShellExecuteW(None, "runas", exe, params, None, 1)
    return rc > 32


def to_long_path(path: str) -> str:
    """Добавляет префикс \\\\?\\ для поддержки путей длиннее 260 символов."""
    if not IS_WIN or path.startswith("\\\\?\\"):
        return path
    if path.startswith("\\\\"):
        return "\\\\?\\UNC\\" + path[2:]
    if len(path) >= 2 and path[1] == ":":
        return "\\\\?\\" + path
    return path


def normalize_root(path: str) -> str:
    """Единый вид корня сканирования (обратные слэши, заглавная буква диска)."""
    p = os.path.normpath(path)
    if IS_WIN:
        if len(p) >= 2 and p[1] == ":":
            p = p[0].upper() + p[1:]
            if len(p) == 2:
                p += "\\"
    return p


def open_path(path: str) -> None:
    if IS_WIN:
        os.startfile(path)  # noqa: S606 - действие пользователя
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def reveal_in_explorer(path: str) -> None:
    if IS_WIN:
        subprocess.Popen(f'explorer /select,"{path}"')
    else:
        open_path(os.path.dirname(path))


def show_native_properties(path: str) -> bool:
    """Стандартное окно «Свойства» Проводника Windows."""
    if not IS_WIN:
        return False
    import ctypes
    from ctypes import wintypes

    class SHELLEXECUTEINFOW(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("fMask", ctypes.c_ulong), ("hwnd", wintypes.HWND),
                    ("lpVerb", wintypes.LPCWSTR), ("lpFile", wintypes.LPCWSTR),
                    ("lpParameters", wintypes.LPCWSTR), ("lpDirectory", wintypes.LPCWSTR),
                    ("nShow", ctypes.c_int), ("hInstApp", wintypes.HINSTANCE),
                    ("lpIDList", ctypes.c_void_p), ("lpClass", wintypes.LPCWSTR),
                    ("hkeyClass", wintypes.HKEY), ("dwHotKey", wintypes.DWORD),
                    ("hIconOrMonitor", wintypes.HANDLE), ("hProcess", wintypes.HANDLE)]

    info = SHELLEXECUTEINFOW()
    info.cbSize = ctypes.sizeof(info)
    info.fMask = 0x0000000C  # SEE_MASK_INVOKEIDLIST
    info.lpVerb = "properties"
    info.lpFile = path
    info.nShow = 1
    return bool(ctypes.windll.shell32.ShellExecuteExW(ctypes.byref(info)))


def default_quick_folders() -> list[str]:
    """Каталоги, которые Windows обычно индексирует: профиль пользователя и меню «Пуск»."""
    home = os.path.expanduser("~")
    cands = [os.path.join(home, n) for n in
             ("Desktop", "Documents", "Downloads", "Pictures", "Videos", "Music", "Source", "source")]
    for env in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if os.environ.get(env):
            cands.append(os.environ[env])
    if IS_WIN:
        cands.append(os.path.join(os.environ.get("APPDATA", ""), "Microsoft", "Windows", "Start Menu"))
        cands.append(os.path.join(os.environ.get("ProgramData", ""), "Microsoft", "Windows", "Start Menu"))
    seen, out = set(), []
    for c in cands:
        if c and os.path.isdir(c):
            n = normalize_root(c)
            if n.lower() not in seen:
                seen.add(n.lower())
                out.append(n)
    if not out:
        out.append(normalize_root(home))
    return out
