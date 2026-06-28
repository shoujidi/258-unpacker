#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ============================================================
# 【密码输入区】—— 需要改密码，只改这里
# ============================================================
PASSWORDS = [
    "孙笑川258",
]

# ============================================================
# 【基础配置区】
# ============================================================
DEFAULT_SCAN_SCRIPT_FOLDER = True
SCAN_FOLDER_DEPTH = 2
MAX_ITEMS_PER_FOLDER = 100
MAX_UNPACK_DEPTH = 20

# 可用内存使用阈值：80%
MEMORY_RATIO = 0.85

# 临时目录模式：
# "same_volume" = 默认，临时目录创建在输出目录同盘，避免跨盘 move 变成复制
# "custom" = 使用 CUSTOM_TEMP_DIR，例如机械硬盘或 RAM Disk
# "system" = 使用系统 Temp，不推荐，仅保留兼容
TEMP_MODE = "same_volume"
CUSTOM_TEMP_DIR = ""
CUSTOM_TEMP_IS_RAMDISK = False

# RAM Disk 自动模式：默认开启。
# Windows 可识别到 DRIVE_RAMDISK 时，会自动使用该盘作为临时目录；
# 未识别到 RAM Disk 时，自动回退 same_volume，不影响原有流程。
AUTO_RAMDISK = True
AUTO_RAMDISK_TEMP_SUBDIR = "._nested_unpacker_ramdisk"
AUTO_RAMDISK_SELECTED_DIR = ""
AUTO_CREATE_RAMDISK = True
AUTO_RAMDISK_TOOL_PATH = ""
AUTO_RAMDISK_SIZE_RATIO = MEMORY_RATIO
AUTO_RAMDISK_MIN_SIZE_GB = 1
AUTO_CREATED_RAMDISK_MOUNT = ""
AUTO_CREATED_RAMDISK_TOOL = ""
AUTO_CREATED_RAMDISK_SIZE_BYTES = 0

# 单个内存对象最大值，避免一次读入过大文件
MAX_MEMORY_OBJECT_BYTES = 4 * 1024 ** 3

# 失败压缩包处理：
# "log_only" = 默认只写 reason.txt，不复制原始压缩包
# "copy" = 复制失败压缩包到 _failed_archives
# "hardlink_or_copy" = 优先硬链接，失败再复制
FAILED_ARCHIVE_MODE = "log_only"

# 普通文件进内存不会减少最终写盘，默认关闭
MEMORY_FOR_NORMAL_FILES = False

# 嵌套压缩包允许内存递归，避免中间压缩包落盘
MEMORY_FOR_NESTED_ARCHIVES = True

# 根压缩包成功解压并输出后，立即删除原始压缩包/整套分卷以释放空间
DELETE_ROOT_ARCHIVES_AFTER_SUCCESS = True

# 失败的压缩包是否保存到 _failed_archives
KEEP_FAILED_ARCHIVES = False

# Windows 双击运行时，结束后是否暂停，方便看日志
PAUSE_WHEN_DONE = False

# 出错弹窗等待时间：15 秒默认忽略继续
ERROR_POPUP_TIMEOUT_SECONDS = 15

# 终端打印更详细处理过程
VERBOSE_DETAIL = False

# 纯绿色运行：不打印终端日志，不写失败日志，不复制失败压缩包。
# 注意：隐藏 ZIP 会通过“文件改名”添加 .zip 后缀，这是专用工具必要步骤，不属于复制。
PURE_GREEN_MODE = True
TERMINAL_LOGS_ENABLED = False
FAILURE_DISK_LOGS_ENABLED = False

# WinRAR / UnRAR 配置。留空自动查找。
# 建议安装 WinRAR，并优先使用 WinRAR.exe。
# 例如：WINRAR_PATH = r"C:\Program Files\WinRAR\WinRAR.exe"
WINRAR_PATH = ""

# 隐藏 ZIP，例如 66_1.mp4 这种，优先用 WinRAR 处理
PREFER_WINRAR_FOR_HIDDEN_ZIP = True

"""
unpacker.py
Python 3.10+

功能：
- 多层嵌套解压 ZIP / RAR / 7Z / 分卷压缩。
- 支持加密，默认使用上方 PASSWORDS。
- 支持 66.MP4 / 66_1.mp4 这类隐藏 ZIP：临时按 .zip 交给 WinRAR / 7-Zip。
- 隐藏 ZIP 优先 WinRAR，因为有些文件只有 WinRAR 能正确处理。
- 错误时弹出提示：忽略继续 / 终止；15 秒不操作默认忽略继续。
- 中间文件优先进入内存；可用内存阈值为 95%。
- 大文件一次进内存后，解压完立即释放；后续继续按当前可用内存判断。
- 分卷压缩会计算整套分卷总大小，可以进内存就整套读入内存，再临时还原给外部工具，用完即删。
- 不传路径时默认扫描脚本所在文件夹，向下两级，每个文件夹最多扫描 100 项。
- 输出到源压缩包同目录；最终单个项目直接输出，多个项目放入最后一层压缩包名称文件夹。

依赖：
- Python 3.10+
- ZIP 可直接处理，但特殊 ZIP 建议安装 WinRAR。
- RAR / 7Z / 分卷 / 部分隐藏 ZIP 需要 7-Zip 或 WinRAR 命令行。
"""

import argparse
import atexit
import ctypes
import gc
import getpass
import io
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path, PurePosixPath

sys.dont_write_bytecode = True


RAR4_MAGIC = b"Rar!\x1a\x07\x00"
RAR5_MAGIC = b"Rar!\x1a\x07\x01\x00"
SEVEN_Z_MAGIC = b"7z\xbc\xaf\x27\x1c"
ZIP_LOCAL_MAGIC = b"PK\x03\x04"
ZIP_EOCD_MAGIC = b"PK\x05\x06"
ZIP64_EOCD_LOCATOR_MAGIC = b"PK\x06\x07"


# ============================================================
# 基础工具
# ============================================================

_ACTIVE_UNPACKER: object | None = None
_CLEANUP_RUNNING = False
_EXIT_HANDLERS_INSTALLED = False
_CONSOLE_CTRL_HANDLER = None


def log(message: str, quiet: bool = False) -> None:
    return


class UserStoppedError(Exception):
    pass


class TaskControl:
    def __init__(self) -> None:
        self.pause_event = threading.Event()
        self.stop_event = threading.Event()
        self.pause_event.set()

    def pause(self) -> None:
        self.pause_event.clear()

    def resume(self) -> None:
        self.pause_event.set()

    def stop(self) -> None:
        self.stop_event.set()
        self.pause_event.set()

    def checkpoint(self) -> None:
        if self.stop_event.is_set():
            raise UserStoppedError()
        self.pause_event.wait()
        if self.stop_event.is_set():
            raise UserStoppedError()


class ConsoleLogger:
    def log(self, message: str) -> None:
        if TERMINAL_LOGS_ENABLED and not PURE_GREEN_MODE:
            print(message, flush=True)


def trim_process_memory() -> None:
    """Ask the OS to reclaim free pages held by the Python process."""
    gc.collect()

    if os.name == "nt":
        try:
            ctypes.windll.kernel32.SetProcessWorkingSetSize.argtypes = [
                ctypes.c_void_p,
                ctypes.c_size_t,
                ctypes.c_size_t,
            ]
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ctypes.windll.kernel32.SetProcessWorkingSetSize(
                handle,
                ctypes.c_size_t(-1).value,
                ctypes.c_size_t(-1).value,
            )
        except Exception:
            pass


def release_memory_now() -> None:
    """Best-effort cleanup for normal exit, errors, Ctrl+C and console close."""
    global _ACTIVE_UNPACKER, _CLEANUP_RUNNING

    if _CLEANUP_RUNNING:
        return

    _CLEANUP_RUNNING = True
    try:
        unpacker = _ACTIVE_UNPACKER
        _ACTIVE_UNPACKER = None

        if unpacker is not None:
            try:
                unpacker.close()
            except Exception:
                pass

        delete_auto_created_ramdisk()
        trim_process_memory()
    finally:
        _CLEANUP_RUNNING = False


def _exit_signal_handler(signum: int, frame: object) -> None:
    release_memory_now()
    if signum == getattr(signal, "SIGINT", None):
        raise KeyboardInterrupt
    raise SystemExit(128 + int(signum))


def _windows_console_ctrl_handler(ctrl_type: int) -> bool:
    release_memory_now()
    return False


def install_exit_cleanup_handlers() -> None:
    global _EXIT_HANDLERS_INSTALLED, _CONSOLE_CTRL_HANDLER

    if _EXIT_HANDLERS_INSTALLED:
        return

    atexit.register(release_memory_now)

    for sig_name in ("SIGINT", "SIGTERM", "SIGBREAK"):
        sig = getattr(signal, sig_name, None)
        if sig is None:
            continue
        try:
            signal.signal(sig, _exit_signal_handler)
        except Exception:
            pass

    if os.name == "nt":
        try:
            handler_type = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_uint)
            _CONSOLE_CTRL_HANDLER = handler_type(_windows_console_ctrl_handler)
            ctypes.windll.kernel32.SetConsoleCtrlHandler(_CONSOLE_CTRL_HANDLER, True)
        except Exception:
            pass

    _EXIT_HANDLERS_INSTALLED = True


def pause_if_needed() -> None:
    if os.name == "nt" and PAUSE_WHEN_DONE:
        try:
            input("\n按 Enter 退出...")
        except Exception:
            pass


def find_7z() -> str | None:
    for name in ("7z", "7zz", "7za", "7z.exe", "7zz.exe", "7za.exe"):
        found = shutil.which(name)
        if found:
            return found

    candidates = [
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def find_winrar() -> str | None:
    """
    自动查找 WinRAR / RAR / UnRAR。
    优先 WinRAR.exe，因为隐藏 ZIP 需要 WinRAR，UnRAR 通常只能处理 RAR。
    """
    if WINRAR_PATH:
        p = Path(WINRAR_PATH)
        if p.exists():
            return str(p)

    for name in ("WinRAR.exe", "WinRAR", "rar.exe", "rar", "UnRAR.exe", "UnRAR"):
        found = shutil.which(name)
        if found:
            return found

    candidates = [
        r"C:\Program Files\WinRAR\WinRAR.exe",
        r"C:\Program Files (x86)\WinRAR\WinRAR.exe",
        r"C:\Program Files\WinRAR\Rar.exe",
        r"C:\Program Files (x86)\WinRAR\Rar.exe",
        r"C:\Program Files\WinRAR\UnRAR.exe",
        r"C:\Program Files (x86)\WinRAR\UnRAR.exe",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def get_available_memory() -> int:
    """返回当前可用物理内存字节数。"""
    if os.name == "nt":
        class MEMORYSTATUSEX(ctypes.Structure):
            _fields_ = [
                ("dwLength", ctypes.c_ulong),
                ("dwMemoryLoad", ctypes.c_ulong),
                ("ullTotalPhys", ctypes.c_ulonglong),
                ("ullAvailPhys", ctypes.c_ulonglong),
                ("ullTotalPageFile", ctypes.c_ulonglong),
                ("ullAvailPageFile", ctypes.c_ulonglong),
                ("ullTotalVirtual", ctypes.c_ulonglong),
                ("ullAvailVirtual", ctypes.c_ulonglong),
                ("sullAvailExtendedVirtual", ctypes.c_ulonglong),
            ]

        stat = MEMORYSTATUSEX()
        stat.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
        try:
            if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(stat)):
                return int(stat.ullAvailPhys)
        except Exception:
            pass

    try:
        pages = os.sysconf("SC_AVPHYS_PAGES")
        page_size = os.sysconf("SC_PAGE_SIZE")
        return int(pages * page_size)
    except Exception:
        return 512 * 1024 * 1024


def find_ramdisk_temp_dir() -> Path | None:
    """Windows: auto-pick the RAM Disk drive with the most free space."""
    if os.name != "nt":
        return None

    try:
        drive_bits = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        drive_bits = 0

    candidates: list[tuple[int, Path]] = []
    for index in range(26):
        if not (drive_bits & (1 << index)):
            continue

        root = f"{chr(ord('A') + index)}:\\"
        try:
            drive_type = ctypes.windll.kernel32.GetDriveTypeW(root)
        except Exception:
            continue

        # DRIVE_RAMDISK = 6
        if drive_type != 6:
            continue

        root_path = Path(root)
        try:
            usage = shutil.disk_usage(root_path)
        except Exception:
            continue

        temp_dir = root_path / AUTO_RAMDISK_TEMP_SUBDIR
        candidates.append((int(usage.free), temp_dir))

    if not candidates:
        return None

    candidates.sort(key=lambda item: item[0], reverse=True)
    return candidates[0][1]


def find_imdisk() -> str | None:
    if AUTO_RAMDISK_TOOL_PATH:
        p = Path(AUTO_RAMDISK_TOOL_PATH)
        if p.exists():
            return str(p)

    for name in ("imdisk.exe", "imdisk"):
        found = shutil.which(name)
        if found:
            return found

    candidates = [
        r"C:\Windows\System32\imdisk.exe",
        r"C:\Program Files\ImDisk\imdisk.exe",
        r"C:\Program Files (x86)\ImDisk\imdisk.exe",
    ]
    for p in candidates:
        if Path(p).exists():
            return p
    return None


def find_free_drive_letter() -> str | None:
    if os.name != "nt":
        return None

    try:
        drive_bits = ctypes.windll.kernel32.GetLogicalDrives()
    except Exception:
        drive_bits = 0

    preferred = list("RZYXWVUTSQPONMLKJIHGFED")
    for letter in preferred:
        index = ord(letter) - ord("A")
        if index < 0 or index >= 26:
            continue
        if not (drive_bits & (1 << index)):
            return f"{letter}:"
    return None


def create_ramdisk_temp_dir(quiet: bool = False) -> tuple[Path | None, str]:
    global AUTO_CREATED_RAMDISK_MOUNT, AUTO_CREATED_RAMDISK_TOOL, AUTO_CREATED_RAMDISK_SIZE_BYTES

    AUTO_CREATED_RAMDISK_MOUNT = ""
    AUTO_CREATED_RAMDISK_TOOL = ""
    AUTO_CREATED_RAMDISK_SIZE_BYTES = 0

    if os.name != "nt":
        return None, "自动创建 RAM Disk 仅支持 Windows"
    if not AUTO_CREATE_RAMDISK:
        return None, "AUTO_CREATE_RAMDISK 已关闭"

    imdisk = find_imdisk()
    if not imdisk:
        return None, "未找到 imdisk.exe，无法自动创建 RAM Disk。请安装 ImDisk Toolkit，或手动创建 RAM Disk"

    mount = find_free_drive_letter()
    if not mount:
        return None, "没有可用盘符用于创建 RAM Disk"

    available = get_available_memory()
    size_bytes = int(available * AUTO_RAMDISK_SIZE_RATIO)
    min_bytes = int(AUTO_RAMDISK_MIN_SIZE_GB * 1024 ** 3)
    if size_bytes < min_bytes:
        return None, f"可用内存不足，无法创建至少 {AUTO_RAMDISK_MIN_SIZE_GB}GB 的 RAM Disk"

    size_mb = max(1, size_bytes // (1024 ** 2))
    cmd = [
        imdisk,
        "-a",
        "-s",
        f"{size_mb}M",
        "-m",
        mount,
        "-p",
        "/fs:ntfs /q /y",
    ]

    log(f"[RAM Disk 自动创建] tool={imdisk} mount={mount} size={format_size(size_mb * 1024 ** 2)}", quiet)
    try:
        proc = subprocess.run(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=120,
        )
    except Exception as e:
        return None, f"调用 ImDisk 失败：{e}"

    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip()
        return None, f"ImDisk 创建失败，return code={proc.returncode} {msg[:1000]}"

    root = Path(mount + "\\")
    for _ in range(50):
        if root.exists():
            break
        time.sleep(0.1)

    if not root.exists():
        return None, f"ImDisk 已返回成功，但盘符未出现：{mount}"

    temp_dir = root / AUTO_RAMDISK_TEMP_SUBDIR
    try:
        temp_dir.mkdir(parents=True, exist_ok=True)
        probe = temp_dir / f".probe_{os.getpid()}.tmp"
        probe.write_bytes(b"ok")
        probe.unlink()
    except Exception as e:
        try:
            subprocess.run([imdisk, "-D", "-m", mount], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except Exception:
            pass
        return None, f"RAM Disk 已创建但不可写：{temp_dir}；{e}"

    AUTO_CREATED_RAMDISK_MOUNT = mount
    AUTO_CREATED_RAMDISK_TOOL = imdisk
    AUTO_CREATED_RAMDISK_SIZE_BYTES = size_mb * 1024 ** 2
    return temp_dir, f"已自动创建 RAM Disk：{mount} size={format_size(AUTO_CREATED_RAMDISK_SIZE_BYTES)}"


def delete_auto_created_ramdisk(quiet: bool = False) -> None:
    global AUTO_CREATED_RAMDISK_MOUNT, AUTO_CREATED_RAMDISK_TOOL, AUTO_CREATED_RAMDISK_SIZE_BYTES

    if not AUTO_CREATED_RAMDISK_MOUNT or not AUTO_CREATED_RAMDISK_TOOL:
        return

    mount = AUTO_CREATED_RAMDISK_MOUNT
    tool = AUTO_CREATED_RAMDISK_TOOL
    log(f"[RAM Disk 自动删除] mount={mount}", quiet)
    try:
        subprocess.run(
            [tool, "-D", "-m", mount],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            errors="replace",
            timeout=60,
        )
    except Exception as e:
        log(f"[RAM Disk 自动删除失败] {mount}: {e}", quiet)
    finally:
        AUTO_CREATED_RAMDISK_MOUNT = ""
        AUTO_CREATED_RAMDISK_TOOL = ""
        AUTO_CREATED_RAMDISK_SIZE_BYTES = 0


def apply_auto_ramdisk_config(quiet: bool = False) -> tuple[bool, str]:
    global TEMP_MODE, CUSTOM_TEMP_DIR, CUSTOM_TEMP_IS_RAMDISK, AUTO_RAMDISK_SELECTED_DIR

    AUTO_RAMDISK_SELECTED_DIR = ""
    if not AUTO_RAMDISK:
        return False, "AUTO_RAMDISK 已关闭"

    # Manual temp configuration wins.
    if TEMP_MODE != "same_volume" or CUSTOM_TEMP_DIR or CUSTOM_TEMP_IS_RAMDISK:
        return True, "已使用手动临时目录配置"

    ramdisk_temp = find_ramdisk_temp_dir()
    if ramdisk_temp is None:
        ramdisk_temp, create_reason = create_ramdisk_temp_dir(quiet)
        if ramdisk_temp is None:
            reason = f"未检测到现有 RAM Disk，且自动创建失败：{create_reason}"
            log(f"[RAM Disk 自动] {reason}", quiet)
            return False, reason
        log(f"[RAM Disk 自动] {create_reason}", quiet)

    try:
        ramdisk_temp.mkdir(parents=True, exist_ok=True)
        probe = ramdisk_temp / f".probe_{os.getpid()}.tmp"
        probe.write_bytes(b"ok")
        probe.unlink()
    except Exception as e:
        reason = f"RAM Disk 临时目录不可写：{ramdisk_temp}；{e}"
        log(f"[RAM Disk 自动] {reason}", quiet)
        return False, reason

    TEMP_MODE = "custom"
    CUSTOM_TEMP_DIR = str(ramdisk_temp)
    CUSTOM_TEMP_IS_RAMDISK = True
    AUTO_RAMDISK_SELECTED_DIR = str(ramdisk_temp)
    log(f"[RAM Disk 自动] 已检测并启用：{ramdisk_temp}", quiet)
    return True, f"已启用 {ramdisk_temp}"


def has_manual_temp_args() -> bool:
    temp_flags = ("--temp-mode", "--temp-dir", "--temp-is-ramdisk")
    for arg in sys.argv[1:]:
        if arg in temp_flags:
            return True
        if any(arg.startswith(flag + "=") for flag in temp_flags):
            return True
    return False


def format_size(size: int) -> str:
    if size >= 1024 ** 3:
        return f"{size / 1024 ** 3:.2f}GB"
    if size >= 1024 ** 2:
        return f"{size / 1024 ** 2:.2f}MB"
    if size >= 1024:
        return f"{size / 1024:.2f}KB"
    return f"{size}B"


def should_use_memory(size: int, purpose: str, quiet: bool = False) -> bool:
    """按用途判断是否值得使用内存，避免把可直接落盘的普通文件多读一遍。"""
    ramdisk_split = purpose == "split_volume" and TEMP_MODE == "custom" and CUSTOM_TEMP_IS_RAMDISK
    if ramdisk_split and CUSTOM_TEMP_DIR:
        try:
            temp_path = Path(CUSTOM_TEMP_DIR)
            usage_path = temp_path if temp_path.exists() else temp_path.parent
            available = int(shutil.disk_usage(usage_path).free)
            limit = available
        except Exception:
            available = get_available_memory()
            limit = int(available * MEMORY_RATIO)
    else:
        available = get_available_memory()
        limit = int(available * MEMORY_RATIO)
    result = False
    reason = "ok"

    if size <= 0:
        reason = "size<=0"
    elif purpose == "split_volume" and not (TEMP_MODE == "custom" and CUSTOM_TEMP_IS_RAMDISK):
        reason = "分卷必须使用 custom RAM Disk 才允许内存临时处理"
    elif size > limit:
        reason = "超过当前可用内存比例限制"
    elif not ramdisk_split and size > MAX_MEMORY_OBJECT_BYTES:
        reason = "超过单对象内存上限"
    elif purpose == "normal_file" and not MEMORY_FOR_NORMAL_FILES:
        reason = "普通文件默认流式写出"
    elif purpose in ("nested_archive", "zip_member_archive") and not MEMORY_FOR_NESTED_ARCHIVES:
        reason = "嵌套压缩包内存处理已关闭"
    else:
        result = True

    log(
        f"[内存判断] purpose={purpose} size={format_size(size)} "
        f"available={format_size(available)} limit={format_size(limit)} "
        f"object_limit={'RAMDISK_DISABLED' if ramdisk_split else format_size(MAX_MEMORY_OBJECT_BYTES)} "
        f"result={'YES' if result else 'NO'} reason={reason}",
        quiet,
    )
    return result


def safe_rel_path(raw: object) -> Path:
    """清理压缩包内部路径，防止绝对路径和 ../ 跳出目录。"""
    s = str(raw).replace("\\", "/")
    parts = []
    for part in PurePosixPath(s).parts:
        if part in ("", ".", "/"):
            continue
        if part == "..":
            continue
        part = part.replace(":", "_")
        part = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", part)
        part = part.strip()
        if part:
            parts.append(part)
    return Path(*parts) if parts else Path("unnamed")


def archive_base_name(name: str) -> str:
    """生成多文件输出文件夹名，不加 _unpacked。"""
    base = Path(name).name.strip()

    # 先去掉人为临时补上的 .zip，例如 66_1.mp4.zip -> 66_1.mp4
    if base.lower().endswith(".zip") and re.search(r"(?i)\.(mp4|mkv|avi|mov|wmv)\.zip$", base):
        base = base[:-4]

    base = re.sub(r"(?i)\.(7z|zip)\.\d{3}$", "", base)
    base = re.sub(r"(?i)\.part0*1\.rar$", "", base)
    base = re.sub(r"(?i)\.r\d{2,}$", "", base)

    archive_exts = [".tar.gz", ".tar.bz2", ".tar.xz", ".zip", ".rar", ".7z"]
    lowered = base.lower()
    for ext in archive_exts:
        if lowered.endswith(ext):
            base = base[: -len(ext)]
            break

    base = base.strip(" .") or "archive"
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", base)
    return base


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path

    parent = path.parent
    stem = path.stem
    suffix = path.suffix

    for i in range(1, 10000):
        candidate = parent / f"{stem}_{i:03d}{suffix}"
        if not candidate.exists():
            return candidate

    raise RuntimeError(f"无法生成唯一文件名：{path}")


def read_file_head(path: Path, n: int = 16) -> bytes:
    try:
        with path.open("rb") as f:
            return f.read(n)
    except Exception:
        return b""


def read_file_tail(path: Path, n: int = 16 * 1024 * 1024) -> bytes:
    try:
        size = path.stat().st_size
        with path.open("rb") as f:
            f.seek(max(0, size - n))
            return f.read(n)
    except Exception:
        return b""


# ============================================================
# 错误处理：弹窗 / 15 秒默认继续
# ============================================================

def ask_ignore_or_abort(
    title: str,
    message: str,
    timeout_seconds: int = ERROR_POPUP_TIMEOUT_SECONDS,
) -> bool:
    """
    返回 True = 忽略继续
    返回 False = 终止
    """
    text = (
        f"{message}\n\n"
        f"选择【是】= 忽略并继续\n"
        f"选择【否】= 终止程序\n\n"
        f"{timeout_seconds} 秒无操作将默认忽略继续。"
    )

    if os.name == "nt":
        try:
            user32 = ctypes.windll.user32
            MessageBoxTimeoutW = user32.MessageBoxTimeoutW
            MessageBoxTimeoutW.argtypes = [
                ctypes.c_void_p,
                ctypes.c_wchar_p,
                ctypes.c_wchar_p,
                ctypes.c_uint,
                ctypes.c_ushort,
                ctypes.c_uint,
            ]
            MessageBoxTimeoutW.restype = ctypes.c_int

            MB_YESNO = 0x00000004
            MB_ICONWARNING = 0x00000030
            MB_SYSTEMMODAL = 0x00001000
            MB_DEFBUTTON1 = 0x00000000
            IDYES = 6
            IDNO = 7
            IDTIMEOUT = 32000

            result = MessageBoxTimeoutW(
                None,
                text,
                title,
                MB_YESNO | MB_ICONWARNING | MB_SYSTEMMODAL | MB_DEFBUTTON1,
                0,
                int(timeout_seconds * 1000),
            )

            if result == IDNO:
                return False
            if result in (IDYES, IDTIMEOUT):
                return True
            return True
        except Exception:
            pass

    if PURE_GREEN_MODE or not TERMINAL_LOGS_ENABLED:
        return True

    return True

    if os.name == "nt":
        try:
            import msvcrt
            start = time.time()
            while time.time() - start < timeout_seconds:
                if msvcrt.kbhit():
                    ch = msvcrt.getwch().lower()
                    if ch == "q":
                        return False
                    return True
                time.sleep(0.1)
            return True
        except Exception:
            return True

    try:
        time.sleep(timeout_seconds)
    except Exception:
        pass
    return True


# ============================================================
# 分卷判断与收集
# ============================================================

def has_pkzip_split_siblings(path: Path) -> bool:
    if path.suffix.lower() != ".zip":
        return False
    return path.with_suffix(".z01").exists()


def is_non_first_volume(path: Path) -> bool:
    name = path.name.lower()

    m = re.search(r"\.(7z|zip)\.(\d{3})$", name)
    if m:
        return int(m.group(2)) > 1

    m = re.search(r"\.part(\d+)\.rar$", name)
    if m:
        return int(m.group(1)) > 1

    if re.search(r"\.r\d{2,}$", name):
        return True

    if re.search(r"\.z\d{2,}$", name):
        return True

    return False


def is_first_split_volume(path: Path) -> bool:
    name = path.name.lower()

    if re.search(r"\.(7z|zip)\.001$", name):
        return True

    if re.search(r"\.part0*1\.rar$", name):
        return True

    if name.endswith(".rar") and path.with_suffix(".r00").exists():
        return True

    if name.endswith(".zip") and has_pkzip_split_siblings(path):
        return True

    return False


def collect_volume_set(first_path: Path) -> list[Path]:
    folder = first_path.parent
    name = first_path.name

    # xxx.7z.001 / xxx.zip.001
    m = re.match(r"(?i)^(.+\.(?:7z|zip))\.(\d{3})$", name)
    if m:
        prefix = m.group(1)
        volumes = []
        for p in folder.iterdir():
            mm = re.match(rf"(?i)^{re.escape(prefix)}\.(\d{{3}})$", p.name)
            if mm and p.is_file():
                volumes.append((int(mm.group(1)), p))
        volumes.sort(key=lambda x: x[0])
        return [p for _, p in volumes]

    # xxx.part1.rar / xxx.part2.rar
    m = re.match(r"(?i)^(.+)\.part0*1\.rar$", name)
    if m:
        prefix = m.group(1)
        volumes = []
        for p in folder.iterdir():
            mm = re.match(rf"(?i)^{re.escape(prefix)}\.part0*(\d+)\.rar$", p.name)
            if mm and p.is_file():
                volumes.append((int(mm.group(1)), p))
        volumes.sort(key=lambda x: x[0])
        return [p for _, p in volumes]

    # xxx.rar + xxx.r00 / xxx.r01
    if first_path.suffix.lower() == ".rar":
        prefix = first_path.with_suffix("").name
        volumes = [(0, first_path)]
        for p in folder.iterdir():
            mm = re.match(rf"(?i)^{re.escape(prefix)}\.r(\d+)$", p.name)
            if mm and p.is_file():
                volumes.append((int(mm.group(1)) + 1, p))
        volumes.sort(key=lambda x: x[0])
        return [p for _, p in volumes]

    # xxx.z01 / xxx.z02 / xxx.zip；7z/WinRAR 应打开 xxx.zip
    if first_path.suffix.lower() == ".zip" and has_pkzip_split_siblings(first_path):
        prefix = first_path.with_suffix("").name
        volumes = []
        for p in folder.iterdir():
            mm = re.match(rf"(?i)^{re.escape(prefix)}\.z(\d+)$", p.name)
            if mm and p.is_file():
                volumes.append((int(mm.group(1)), p))
        volumes.sort(key=lambda x: x[0])
        volumes.append((999999, first_path))
        return [p for _, p in volumes]

    return [first_path]


def volume_set_total_size(volumes: list[Path]) -> int:
    total = 0
    for p in volumes:
        try:
            total += p.stat().st_size
        except Exception:
            pass
    return total


def materialize_volume_set(volume_data: list[tuple[str, bytes]], target_dir: Path) -> Path:
    raise RuntimeError("禁止将整套分卷 bytes 写回临时目录；请使用 materialize_volume_set_from_paths 流式写入 RAM Disk")
    target_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for filename, data in volume_data:
        out = target_dir / safe_rel_path(filename).name
        raise RuntimeError("禁止从 bytes 材料化整套分卷，避免额外写盘")
        paths.append(out)

    if not paths:
        raise RuntimeError("分卷内存数据为空")

    zip_candidates = [p for p in paths if p.suffix.lower() == ".zip"]
    if zip_candidates and any(re.search(r"\.z\d+$", p.name.lower()) for p in paths):
        return zip_candidates[0]

    return paths[0]


def materialize_volume_set_from_paths(volumes: list[Path], target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for src in volumes:
        out = target_dir / safe_rel_path(src.name).name
        with src.open("rb") as inp, out.open("wb") as dst:
            shutil.copyfileobj(inp, dst, length=1024 * 1024)
        paths.append(out)

    if not paths:
        raise RuntimeError("分卷列表为空")

    zip_candidates = [p for p in paths if p.suffix.lower() == ".zip"]
    if zip_candidates and any(re.search(r"\.z\d+$", p.name.lower()) for p in paths):
        return zip_candidates[0]

    return paths[0]


def delete_volume_set(volumes: list[Path]) -> None:
    for p in volumes:
        try:
            if p.exists():
                log(f"[清理分卷] {p}")
                p.unlink()
        except Exception:
            pass


def delete_archive_source(path: Path) -> None:
    if is_first_split_volume(path):
        delete_volume_set(collect_volume_set(path))
        return
    try:
        if path.exists():
            log(f"[清理源压缩包] {path}")
            path.unlink()
    except Exception:
        pass


# ============================================================
# 隐藏 ZIP 检测
# ============================================================

def has_zip_tail_signature_from_bytes(data: bytes) -> bool:
    if not data:
        return False

    tail = data[-16 * 1024 * 1024:]
    if ZIP_EOCD_MAGIC in tail or ZIP64_EOCD_LOCATOR_MAGIC in tail:
        return ZIP_LOCAL_MAGIC in data or ZIP_LOCAL_MAGIC in tail
    return False


def has_zip_tail_signature_from_path(path: Path) -> bool:
    tail = read_file_tail(path)
    if not (ZIP_EOCD_MAGIC in tail or ZIP64_EOCD_LOCATOR_MAGIC in tail):
        return False

    head = read_file_head(path, 4 * 1024 * 1024)
    return ZIP_LOCAL_MAGIC in tail or ZIP_LOCAL_MAGIC in head


def is_zip_like_extension(name: str) -> bool:
    n = name.lower()
    return n.endswith(".zip") or re.search(r"\.zip\.\d{3}$", n) is not None


def name_suggests_archive(name: str) -> bool:
    n = name.lower()
    if is_zip_like_extension(n):
        return True
    if n.endswith(".rar") or n.endswith(".7z"):
        return True
    if re.search(r"\.(7z|zip)\.001$", n):
        return True
    if re.search(r"\.part0*1\.rar$", n):
        return True
    return False


def should_skip_scan_path(path: Path) -> bool:
    """Skip script/cache artifacts so default folder scans do not unpack our own files."""
    lower_parts = {part.lower() for part in path.parts}
    if "__pycache__" in lower_parts:
        return True

    name = path.name.lower()
    if name in {"unpacker.py"}:
        return True

    ignored_suffixes = (
        ".py",
        ".pyc",
        ".pyo",
        ".bak",
        ".tmp",
        ".log",
    )
    return name.endswith(ignored_suffixes)


def detect_format(
    name: str,
    path: Path | None = None,
    data: bytes | None = None,
) -> str | None:
    """
    返回：
    - zip
    - hidden_zip
    - rar
    - 7z
    - split
    - None
    """
    lower_name = name.lower()

    if lower_name.endswith((".py", ".pyc", ".pyo")):
        return None

    if path is not None and should_skip_scan_path(path):
        return None

    if path is not None and is_first_split_volume(path):
        return "split"

    if re.search(r"\.(7z|zip)\.001$", lower_name):
        return "split"

    if re.search(r"\.part0*1\.rar$", lower_name):
        return "split"

    # ZIP 内容识别，包括 MP4+ZIP
    try:
        if data is not None:
            if zipfile.is_zipfile(io.BytesIO(data)):
                return "zip" if is_zip_like_extension(name) else "hidden_zip"
        elif path is not None:
            if zipfile.is_zipfile(path):
                return "zip" if is_zip_like_extension(name) else "hidden_zip"
    except Exception:
        pass

    if data is not None:
        head = data[:16]
    elif path is not None:
        head = read_file_head(path, 16)
    else:
        head = b""

    if head.startswith(SEVEN_Z_MAGIC):
        return "7z"

    if head.startswith(RAR4_MAGIC) or head.startswith(RAR5_MAGIC):
        return "rar"

    if lower_name.endswith(".7z"):
        return "7z"

    if lower_name.endswith(".rar"):
        return "rar"

    return None


# ============================================================
# 文件夹扫描与失败保存
# ============================================================

def list_folder_limited(
    root: Path,
    max_depth: int,
    max_items: int,
    quiet: bool,
) -> list[Path]:
    found: list[Path] = []

    def walk(folder: Path, depth: int) -> None:
        try:
            items = sorted(folder.iterdir(), key=lambda p: p.name.lower())
        except Exception as e:
            log(f"[跳过目录] 无法读取：{folder}，原因：{e}", quiet)
            return

        if len(items) > max_items:
            log(f"[限制] {folder} 内项目超过 {max_items}，只扫描前 {max_items} 项。", quiet)
            items = items[:max_items]

        for p in items:
            if should_skip_scan_path(p):
                log(f"[跳过程序文件] {p}", quiet)
                continue

            if p.is_file():
                found.append(p)
            elif p.is_dir() and depth < max_depth:
                walk(p, depth + 1)

    walk(root, 0)
    return found


def path_size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for p in path.rglob("*"):
            if p.is_file():
                total += p.stat().st_size
        return total
    except Exception:
        return 0


def same_volume(a: Path, b: Path) -> bool:
    try:
        if os.name == "nt":
            return a.resolve().drive.lower() == b.resolve().drive.lower()
        return os.stat(a if a.exists() else a.parent).st_dev == os.stat(b if b.exists() else b.parent).st_dev
    except Exception:
        return False


def move_entry(src: Path, target_dir: Path) -> Path:
    target_dir.mkdir(parents=True, exist_ok=True)
    dest = unique_path(target_dir / src.name)
    if not same_volume(src, dest):
        log(f"[跨盘移动警告] 将发生复制写入：{src} -> {dest}")
    shutil.move(str(src), str(dest))
    return dest


def write_failure_reason(
    failed_dir: Path,
    name: str,
    reason: str,
    path: Path | None = None,
    data: bytes | None = None,
    depth: int | None = None,
) -> None:
    if PURE_GREEN_MODE or not FAILURE_DISK_LOGS_ENABLED:
        return
    failed_dir.mkdir(parents=True, exist_ok=True)
    volumes = collect_volume_set(path) if path is not None and path.exists() and is_first_split_volume(path) else []
    size = volume_set_total_size(volumes) if volumes else (path.stat().st_size if path is not None and path.exists() else (len(data) if data else 0))
    lines = [
        f"失败时间: {time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"源文件路径: {path if path is not None else '<memory>'}",
        f"文件名: {name}",
        f"格式: {detect_format(name, path=path, data=data) or 'unknown'}",
        f"大小: {format_size(size)}",
        f"错误原因: {reason}",
        f"处理层级: {'' if depth is None else depth}",
        f"是否分卷: {'是' if volumes else '否'}",
        "分卷列表:",
    ]
    lines.extend([f"  {p}" for p in volumes])
    reason_file = unique_path(failed_dir / f"{archive_base_name(name)}.reason.txt")
    return
    log(f"[失败记录] {reason_file}")


def hardlink_or_copy(src: Path, dest: Path) -> str:
    return "skipped"
    dest = unique_path(dest)
    try:
        return "skipped"
        return "hardlink"
    except Exception:
        return "skipped"
        return "copy"


def copy_failed_archive(
    failed_dir: Path,
    name: str,
    path: Path | None = None,
    data: bytes | None = None,
    reason: str = "",
    depth: int | None = None,
) -> int:
    if PURE_GREEN_MODE or not KEEP_FAILED_ARCHIVES:
        return 0

    written = 0
    try:
        write_failure_reason(failed_dir, name, reason, path=path, data=data, depth=depth)

        if FAILED_ARCHIVE_MODE == "log_only" or not KEEP_FAILED_ARCHIVES:
            log(f"[失败归档] log_only：不复制失败压缩包 {name}")
            return written

        if path is not None and path.exists():
            if is_first_split_volume(path):
                for vp in collect_volume_set(path):
                    if vp.exists():
                        if FAILED_ARCHIVE_MODE == "hardlink_or_copy":
                            mode = hardlink_or_copy(vp, failed_dir / vp.name)
                            log(f"[失败归档] {mode}: {vp.name}")
                            if mode == "copy":
                                written += vp.stat().st_size
                        else:
                            pass
                            log(f"[失败归档] copy: {vp.name}")
                            written += vp.stat().st_size
            else:
                if FAILED_ARCHIVE_MODE == "hardlink_or_copy":
                    mode = hardlink_or_copy(path, failed_dir / safe_rel_path(name).name)
                    log(f"[失败归档] {mode}: {path.name}")
                    if mode == "copy":
                        written += path.stat().st_size
                else:
                    pass
                    log(f"[失败归档] copy: {path.name}")
                    written += path.stat().st_size

        elif data is not None:
            out = unique_path(failed_dir / safe_rel_path(name).name)
            pass
            log(f"[失败归档] memory-copy: {out.name}")
            written += len(data)

    except Exception:
        return written

    return written


class TempManager:
    def __init__(self, mode: str, custom_dir: str, custom_is_ramdisk: bool, quiet: bool) -> None:
        self.mode = mode
        self.custom_dir = Path(custom_dir).expanduser() if custom_dir else None
        self.custom_is_ramdisk = custom_is_ramdisk
        self.quiet = quiet
        self._system_roots: dict[Path, tempfile.TemporaryDirectory] = {}

    def root_for_output(self, output_dir: Path) -> Path:
        if self.mode == "custom":
            if self.custom_dir is None:
                raise RuntimeError("--temp-mode custom 需要同时指定 --temp-dir")
            return self.custom_dir.resolve() / "._nested_unpacker_work"
        if self.mode == "system":
            obj = tempfile.TemporaryDirectory(prefix="nested_unpacker_")
            root = Path(obj.name)
            self._system_roots[root] = obj
            return root
        return output_dir.resolve() / "._nested_unpacker_work"

    def create_root_work(self, source_path: Path, output_dir: Path) -> Path:
        work_root = self.root_for_output(output_dir)
        root_name = f"root_{abs(hash(str(source_path.resolve()))) & 0xffffffff:x}"
        root_work = work_root / root_name
        if root_work.exists():
            shutil.rmtree(root_work, ignore_errors=True)
        root_work.mkdir(parents=True, exist_ok=True)
        log(
            f"[临时目录] mode={self.mode} temp_root={work_root} "
            f"ramdisk={self.custom_is_ramdisk} output={output_dir} "
            f"same_volume={same_volume(work_root, output_dir)}",
            self.quiet,
        )
        return root_work

    def cleanup_root_work(self, root_work: Path) -> None:
        work_root = root_work.parent
        shutil.rmtree(root_work, ignore_errors=True)
        try:
            if self.mode in ("same_volume", "custom") and work_root.exists() and not any(work_root.iterdir()):
                work_root.rmdir()
        except Exception:
            pass
        for root, obj in list(self._system_roots.items()):
            try:
                if root == root_work or root in root_work.parents:
                    obj.cleanup()
                    self._system_roots.pop(root, None)
            except Exception:
                pass

    def close(self) -> None:
        for root, obj in list(self._system_roots.items()):
            try:
                obj.cleanup()
            except Exception:
                pass
            self._system_roots.pop(root, None)


# ============================================================
# 解压核心
# ============================================================

class NestedUnpacker:
    def __init__(
        self,
        passwords: list[str],
        sevenzip_path: str | None = None,
        winrar_path: str | None = None,
        quiet: bool = False,
        output_override: Path | None = None,
        logger: object | None = None,
        control: TaskControl | None = None,
    ) -> None:
        self.passwords: list[str] = []
        for p in passwords:
            if p and p not in self.passwords:
                self.passwords.append(p)

        self.quiet = quiet
        self.sevenzip_path = sevenzip_path or find_7z()
        self.winrar_path = winrar_path or find_winrar()
        self.output_override = output_override
        self.logger = logger
        self.control = control

        self.temp_manager = TempManager(TEMP_MODE, CUSTOM_TEMP_DIR, CUSTOM_TEMP_IS_RAMDISK, quiet)
        self.temp_root: Path | None = None

        self.total_archives = 0
        self.total_outputs = 0
        self.total_failed = 0
        self.total_skipped = 0
        self.estimated_temp_write_bytes = 0
        self.estimated_avoided_write_bytes = 0

        self.log("========== 工具检测 ==========")
        self.log(f"[工具] Python: 可用")
        self.log(f"[工具] 7-Zip: {self.sevenzip_path or '未找到'}")
        self.log(f"[工具] WinRAR/RAR/UnRAR: {self.winrar_path or '未找到'}")
        self.log(f"[配置] 内存阈值: 当前可用内存的 {int(MEMORY_RATIO * 100)}%")
        self.log(f"[配置] 单个内存对象上限: {format_size(MAX_MEMORY_OBJECT_BYTES)}")
        self.log(f"[配置] 临时目录模式: {TEMP_MODE}")
        self.log(f"[配置] 自定义临时目录: {CUSTOM_TEMP_DIR or '<未设置>'}")
        self.log(f"[配置] 自定义临时目录是否 RAM Disk: {CUSTOM_TEMP_IS_RAMDISK}")
        self.log(f"[配置] 自动 RAM Disk 目录: {AUTO_RAMDISK_SELECTED_DIR or '<未启用>'}")
        self.log(f"[配置] 失败压缩包模式: {FAILED_ARCHIVE_MODE}")
        self.log(f"[配置] 成功后删除源压缩包: {DELETE_ROOT_ARCHIVES_AFTER_SUCCESS}")
        self.log(f"[配置] 密码数量: {len(self.passwords)}")

    def close(self) -> None:
        try:
            if self.temp_root is not None:
                self.temp_manager.cleanup_root_work(self.temp_root)
                self.temp_root = None
            self.temp_manager.close()
        except Exception:
            pass
        try:
            self.passwords.clear()
        except Exception:
            pass
        gc.collect()

    def log(self, message: str) -> None:
        if self.logger is not None:
            try:
                self.logger.log(message)
            except Exception:
                pass
            return
        log(message, self.quiet)

    def detail(self, message: str) -> None:
        if VERBOSE_DETAIL:
            self.log(message)

    def checkpoint(self) -> None:
        if self.control is not None:
            self.control.checkpoint()

    def run(self, inputs: list[Path]) -> None:
        roots: list[Path] = []

        for p in inputs:
            p = p.resolve()

            if not p.exists():
                self.log(f"[跳过] 不存在：{p}")
                continue

            if p.is_dir():
                self.log(f"[扫描目录] {p}")
                roots.extend(
                    list_folder_limited(
                        p,
                        SCAN_FOLDER_DEPTH,
                        MAX_ITEMS_PER_FOLDER,
                        self.quiet,
                    )
                )
            else:
                roots.append(p)

        if not roots:
            self.log("[结束] 没有找到任何文件。")
            return

        unique_roots: list[Path] = []
        seen = set()

        for p in roots:
            try:
                key = str(p.resolve()).lower()
            except Exception:
                key = str(p).lower()

            if key not in seen:
                seen.add(key)
                unique_roots.append(p)

        self.log(f"[扫描完成] 待检查文件数量：{len(unique_roots)}")

        for index, p in enumerate(sorted(unique_roots, key=lambda x: str(x).lower()), start=1):
            self.checkpoint()
            self.log(f"\n========== 文件 {index}/{len(unique_roots)} ==========")
            self.log(f"[当前文件] {p}")

            if should_skip_scan_path(p):
                self.total_skipped += 1
                self.log(f"[跳过程序文件] {p}")
                continue

            if is_non_first_volume(p):
                self.total_skipped += 1
                self.log(f"[跳过分卷] {p}")
                continue

            self.process_root_file(p)

        self.log("\n========== 处理完成 ==========")
        self.log(f"已处理压缩包层数：{self.total_archives}")
        self.log(f"最终输出项目数：{self.total_outputs}")
        self.log(f"失败压缩包数量：{self.total_failed}")
        self.log(f"跳过文件数量：{self.total_skipped}")
        self.log("\n========== 写入优化统计 ==========")
        self.log(f"临时目录模式：{TEMP_MODE}")
        self.log(f"失败文件模式：{FAILED_ARCHIVE_MODE}")
        self.log(f"估算避免中间写入：{format_size(self.estimated_avoided_write_bytes)}")
        self.log(f"估算额外临时写入：{format_size(self.estimated_temp_write_bytes)}")
        self.log("注意：最终输出文件写入不可避免，未计入额外写入。")

    def process_root_file(self, path: Path) -> None:
        fmt = detect_format(path.name, path=path)

        if fmt is None:
            self.total_skipped += 1
            self.detail(f"[不是压缩包] {path.name}")
            return

        output_dir = self.output_override if self.output_override else path.parent
        output_dir.mkdir(parents=True, exist_ok=True)

        staging = self.temp_manager.create_root_work(path, output_dir)
        self.temp_root = staging

        self.log(f"[开始处理] {path}")
        self.log(f"[识别格式] {fmt}")

        try:
            self.expand_archive(
                archive_name=path.name,
                target_dir=staging,
                depth=0,
                source_path=path,
                source_data=None,
                delete_source_after=False,
                root_output_dir=output_dir,
            )

            final_entries = sorted(staging.iterdir(), key=lambda x: x.name.lower())

            if not final_entries:
                self.log(f"[空压缩包] {path.name}")
                return

            for entry in final_entries:
                self.checkpoint()
                if not same_volume(entry, output_dir):
                    self.estimated_temp_write_bytes += path_size(entry)
                moved = move_entry(entry, output_dir)
                self.total_outputs += 1
                self.log(f"[输出] {moved}")

            if DELETE_ROOT_ARCHIVES_AFTER_SUCCESS:
                delete_archive_source(path)

        except SystemExit:
            raise

        except UserStoppedError:
            raise

        except Exception as e:
            self.total_failed += 1
            self.estimated_temp_write_bytes += copy_failed_archive(
                output_dir / "_failed_archives",
                path.name,
                path=path,
                reason=str(e),
                depth=0,
            )

            go_on = ask_ignore_or_abort(
                "解压错误",
                f"文件：{path}\n\n错误：{e}",
            )
            if not go_on:
                raise SystemExit(1)

        finally:
            self.temp_manager.cleanup_root_work(staging)
            self.temp_root = None
            gc.collect()

    def expand_archive(
        self,
        archive_name: str,
        target_dir: Path,
        depth: int,
        source_path: Path | None = None,
        source_data: bytes | None = None,
        delete_source_after: bool = False,
        root_output_dir: Path | None = None,
    ) -> list[Path]:
        if depth > MAX_UNPACK_DEPTH:
            raise RuntimeError(f"超过最大嵌套层数 {MAX_UNPACK_DEPTH}: {archive_name}")

        self.checkpoint()
        fmt = detect_format(archive_name, path=source_path, data=source_data)
        if fmt is None:
            raise RuntimeError(f"不是可识别压缩包：{archive_name}")

        self.total_archives += 1

        display_name = archive_name
        if fmt == "hidden_zip" and not archive_name.lower().endswith(".zip"):
            display_name = archive_name + ".zip"

        size = self.source_size(fmt, source_path, source_data)
        self.log(
            f"[解压] depth={depth} fmt={fmt} "
            f"size={format_size(size)} name={archive_name}"
        )

        if self.temp_root is None:
            raise RuntimeError("临时工作目录尚未初始化")

        extract_dir = self.temp_root / f"extract_{self.total_archives}_{abs(hash((archive_name, depth))) & 0xffffffff:x}"
        if extract_dir.exists():
            shutil.rmtree(extract_dir, ignore_errors=True)
        extract_dir.mkdir(parents=True, exist_ok=True)

        try:
            self.extract_archive_to_dir(
                archive_name=archive_name,
                fmt=fmt,
                extract_dir=extract_dir,
                source_path=source_path,
                source_data=source_data,
                depth=depth,
                root_output_dir=root_output_dir,
            )

            if delete_source_after and source_path is not None:
                delete_archive_source(source_path)

            self.expand_nested_archives_in_place(
                extract_dir,
                depth + 1,
                root_output_dir,
            )

            top_entries = sorted(extract_dir.iterdir(), key=lambda x: x.name.lower())

            if not top_entries:
                return []

            if len(top_entries) == 1:
                return [move_entry(top_entries[0], target_dir)]

            bundle_dir = unique_path(target_dir / archive_base_name(display_name))
            bundle_dir.mkdir(parents=True, exist_ok=True)

            for entry in top_entries:
                move_entry(entry, bundle_dir)

            return [bundle_dir]

        finally:
            shutil.rmtree(extract_dir, ignore_errors=True)
            gc.collect()

    def source_size(
        self,
        fmt: str,
        source_path: Path | None,
        source_data: bytes | None,
    ) -> int:
        if source_data is not None:
            return len(source_data)

        if source_path is None or not source_path.exists():
            return 0

        try:
            if fmt == "split" and is_first_split_volume(source_path):
                return volume_set_total_size(collect_volume_set(source_path))
            return source_path.stat().st_size
        except Exception:
            return 0

    def expand_nested_archives_in_place(
        self,
        folder: Path,
        depth: int,
        root_output_dir: Path | None,
    ) -> None:
        while True:
            changed = False

            files = sorted(
                [p for p in folder.rglob("*") if p.is_file()],
                key=lambda x: str(x).lower(),
            )

            for p in files:
                self.checkpoint()
                if not p.exists():
                    continue

                if should_skip_scan_path(p):
                    continue

                if is_non_first_volume(p):
                    continue

                fmt = detect_format(p.name, path=p)
                if fmt is None:
                    continue

                self.log(f"[发现嵌套压缩包] depth={depth} fmt={fmt} file={p}")
                parent = p.parent

                try:
                    if fmt == "split" and is_first_split_volume(p):
                        self.expand_split_archive_from_path(
                            p,
                            parent,
                            depth,
                            root_output_dir,
                        )
                    else:
                        self.expand_normal_archive_from_path(
                            p,
                            parent,
                            depth,
                            root_output_dir,
                        )

                except SystemExit:
                    raise

                except UserStoppedError:
                    raise

                except Exception as e:
                    self.total_failed += 1

                    if root_output_dir is not None:
                        self.estimated_temp_write_bytes += copy_failed_archive(
                            root_output_dir / "_failed_archives",
                            p.name,
                            path=p,
                            reason=str(e),
                            depth=depth,
                        )

                    go_on = ask_ignore_or_abort(
                        "嵌套解压错误",
                        f"文件：{p}\n\n错误：{e}",
                    )
                    if not go_on:
                        raise SystemExit(1)

                    try:
                        if p.exists():
                            p.unlink()
                    except Exception:
                        pass

                changed = True
                break

            if not changed:
                break

    def expand_split_archive_from_path(
        self,
        p: Path,
        parent: Path,
        depth: int,
        root_output_dir: Path | None,
    ) -> None:
        volumes = collect_volume_set(p)
        total_size = volume_set_total_size(volumes)

        if should_use_memory(total_size, "split_volume", self.quiet):
            if self.temp_root is None:
                raise RuntimeError("临时工作目录尚未初始化")
            self.log(
                f"[分卷RAM临时处理] volumes={len(volumes)} "
                f"total={format_size(total_size)} name={p.name}"
            )

            temp_volume_dir: Path | None = None
            success = False

            try:
                temp_volume_dir = self.temp_root / f"mem_volumes_{abs(hash(str(p))) & 0xffffffff:x}"
                if temp_volume_dir.exists():
                    shutil.rmtree(temp_volume_dir, ignore_errors=True)

                archive_path = materialize_volume_set_from_paths(volumes, temp_volume_dir)

                self.expand_archive(
                    archive_name=p.name,
                    target_dir=parent,
                    depth=depth,
                    source_path=archive_path,
                    source_data=None,
                    delete_source_after=False,
                    root_output_dir=root_output_dir,
                )
                success = True

            finally:
                if temp_volume_dir is not None:
                    shutil.rmtree(temp_volume_dir, ignore_errors=True)
                if success:
                    delete_volume_set(volumes)
                gc.collect()

        else:
            self.log(f"[分卷直接解压] total={format_size(total_size)} name={p.name}")
            self.expand_archive(
                archive_name=p.name,
                target_dir=parent,
                depth=depth,
                source_path=p,
                source_data=None,
                delete_source_after=True,
                root_output_dir=root_output_dir,
            )

    def expand_normal_archive_from_path(
        self,
        p: Path,
        parent: Path,
        depth: int,
        root_output_dir: Path | None,
    ) -> None:
        size = p.stat().st_size if p.exists() else 0

        fmt = detect_format(p.name, path=p)

        # 专用隐藏 ZIP 必须保留为硬盘文件，才能通过“改名为 .zip -> WinRAR”处理。
        # 不允许先读入内存，否则无法执行 Windows 文件改名逻辑。
        if fmt != "hidden_zip" and should_use_memory(size, "nested_archive", self.quiet):
            self.log(f"[进内存] size={format_size(size)} name={p.name}")
            data = p.read_bytes()

            try:
                try:
                    p.unlink()
                except Exception:
                    pass

                self.expand_archive(
                    archive_name=p.name,
                    target_dir=parent,
                    depth=depth,
                    source_path=None,
                    source_data=data,
                    delete_source_after=False,
                    root_output_dir=root_output_dir,
                )
                self.estimated_avoided_write_bytes += size

            finally:
                del data
                gc.collect()

        else:
            self.log(f"[落盘处理] size={format_size(size)} name={p.name}")
            self.expand_archive(
                archive_name=p.name,
                target_dir=parent,
                depth=depth,
                source_path=p,
                source_data=None,
                delete_source_after=True,
                root_output_dir=root_output_dir,
            )

    def extract_archive_to_dir(
        self,
        archive_name: str,
        fmt: str,
        extract_dir: Path,
        source_path: Path | None,
        source_data: bytes | None,
        depth: int,
        root_output_dir: Path | None,
    ) -> None:
        """
        专用工具解压顺序：

        1. hidden_zip：
           例如 66_1.mp4 / 66.mp4。
           只做一件事：原文件改名为 .zip -> WinRAR 解压 -> 成功后删除 .zip 源文件。
           不走 7z，不走 Python zipfile，不恢复原名。

        2. 普通 zip：
           先 Python zipfile。
           Python 失败后最多 WinRAR 兜底。
           不走 7z。

        3. 其他格式：
           本专用工具不再作为通用 RAR / 7Z / 分卷解压器处理。
        """
        src_display = source_path if source_path else f"<memory:{archive_name}>"
        self.detail(f"[准备解压] fmt={fmt} source={src_display} target={extract_dir}")

        if fmt == "hidden_zip":
            if source_path is None:
                raise RuntimeError(f"隐藏 ZIP 必须来自硬盘文件，当前来源是内存：{archive_name}")

            self.log(f"[专用隐藏ZIP] {archive_name}：改名为 .zip 后只使用 WinRAR 解压")
            self.extract_by_winrar_to_dir(
                archive_name=archive_name,
                fmt=fmt,
                extract_dir=extract_dir,
                source_path=source_path,
                source_data=None,
            )
            return

        if fmt == "zip":
            errors = []

            try:
                self.log(f"[Python ZIP] {archive_name}")
                self.extract_zip_to_dir(archive_name, extract_dir, source_path, source_data, depth, root_output_dir)
                return
            except Exception as e:
                errors.append(f"Python zipfile失败：{e}")
                self.log(f"[Python ZIP失败] {archive_name}: {e}")

            try:
                self.log(f"[WinRAR ZIP兜底] {archive_name}")
                self.extract_by_winrar_to_dir(archive_name, fmt, extract_dir, source_path, source_data)
                return
            except Exception as e:
                errors.append(f"WinRAR失败：{e}")
                self.log(f"[WinRAR失败] {archive_name}: {e}")

            raise RuntimeError("\n".join(errors))

        raise RuntimeError(
            f"当前专用工具只处理 ZIP / hidden_zip，不处理格式：{fmt}，文件：{archive_name}"
        )

    def extract_zip_to_dir(
        self,
        archive_name: str,
        extract_dir: Path,
        source_path: Path | None,
        source_data: bytes | None,
        depth: int,
        root_output_dir: Path | None,
    ) -> None:
        if source_data is not None:
            zf_source = io.BytesIO(source_data)
            held_data = None

        elif source_path is not None:
            held_data = None
            zf_source = source_path
        else:
            raise RuntimeError("ZIP 来源为空")

        try:
            with zipfile.ZipFile(zf_source, "r") as zf:
                info_list = zf.infolist()
                for index, info in enumerate(info_list, start=1):
                    self.checkpoint()
                    self.detail(
                        f"[ZIP内部文件] archive={archive_name} "
                        f"member={index}/{len(info_list)} name={info.filename} "
                        f"size={format_size(int(info.file_size or 0))}"
                    )

                    rel = safe_rel_path(info.filename)
                    out_path = extract_dir / rel

                    if info.is_dir():
                        out_path.mkdir(parents=True, exist_ok=True)
                        continue

                    out_path.parent.mkdir(parents=True, exist_ok=True)

                    encrypted = bool(info.flag_bits & 0x1)
                    member_size = int(info.file_size or 0)

                    may_be_archive = name_suggests_archive(rel.name) or self.zip_member_has_archive_head(zf, info, encrypted)

                    if (
                        member_size > 0
                        and may_be_archive
                        and should_use_memory(member_size, "zip_member_archive", self.quiet)
                    ):
                        data = self.read_zip_member_to_bytes(zf, info, encrypted)

                        try:
                            child_fmt = detect_format(rel.name, data=data)

                            if child_fmt == "hidden_zip":
                                # 专用隐藏 ZIP 必须落成硬盘文件后改名 .zip，再交给 WinRAR。
                                dest = unique_path(out_path)
                                dest.write_bytes(data)
                                self.expand_archive(
                                    archive_name=dest.name,
                                    target_dir=dest.parent,
                                    depth=depth + 1,
                                    source_path=dest,
                                    source_data=None,
                                    delete_source_after=False,
                                    root_output_dir=root_output_dir,
                                )
                            elif child_fmt is not None and child_fmt != "split":
                                self.estimated_avoided_write_bytes += member_size
                                self.expand_archive(
                                    archive_name=rel.name,
                                    target_dir=out_path.parent,
                                    depth=depth + 1,
                                    source_path=None,
                                    source_data=data,
                                    delete_source_after=False,
                                    root_output_dir=root_output_dir,
                                )
                            else:
                                dest = unique_path(out_path)
                                dest.write_bytes(data)

                        finally:
                            del data
                            gc.collect()

                    else:
                        dest = unique_path(out_path)
                        self.extract_zip_member_to_file(zf, info, encrypted, dest)
        finally:
            try:
                del held_data
            except Exception:
                pass
            gc.collect()

    def zip_member_has_archive_head(
        self,
        zf: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        encrypted: bool,
    ) -> bool:
        head = b""
        try:
            if not encrypted:
                with zf.open(info, "r") as src:
                    head = src.read(16)
            else:
                for pwd in self.passwords:
                    try:
                        with zf.open(info, "r", pwd=pwd.encode("utf-8")) as src:
                            head = src.read(16)
                        break
                    except Exception:
                        continue
        except Exception:
            return False

        return (
            head.startswith(ZIP_LOCAL_MAGIC)
            or head.startswith(SEVEN_Z_MAGIC)
            or head.startswith(RAR4_MAGIC)
            or head.startswith(RAR5_MAGIC)
        )

    def read_zip_member_to_bytes(
        self,
        zf: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        encrypted: bool,
    ) -> bytes:
        if not encrypted:
            try:
                return zf.read(info)
            except Exception as e:
                raise RuntimeError(
                    f"ZIP 文件读取失败：{info.filename}; "
                    f"可能原因：压缩算法不支持、文件损坏、或需要外部工具。原始错误：{e}"
                ) from e

        if not self.passwords:
            raise RuntimeError(f"ZIP 文件需要密码：{info.filename}")

        last_error: Exception | None = None
        for pwd in self.passwords:
            try:
                return zf.read(info, pwd=pwd.encode("utf-8"))
            except Exception as e:
                last_error = e

        raise RuntimeError(
            f"ZIP 文件读取失败：{info.filename}; "
            f"可能原因：密码错误、AES加密不支持、压缩算法不支持、文件损坏。"
            f"原始错误：{last_error}"
        )

    def extract_zip_member_to_file(
        self,
        zf: zipfile.ZipFile,
        info: zipfile.ZipInfo,
        encrypted: bool,
        dest: Path,
    ) -> None:
        if not encrypted:
            try:
                with zf.open(info, "r") as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out, length=1024 * 1024)
                return
            except Exception as e:
                try:
                    if dest.exists():
                        dest.unlink()
                except Exception:
                    pass
                raise RuntimeError(
                    f"ZIP 文件解压失败：{info.filename}; "
                    f"可能原因：压缩算法不支持、文件损坏、或需要外部工具。原始错误：{e}"
                ) from e

        if not self.passwords:
            raise RuntimeError(f"ZIP 文件需要密码：{info.filename}")

        last_error: Exception | None = None
        for pwd in self.passwords:
            try:
                with zf.open(info, "r", pwd=pwd.encode("utf-8")) as src, dest.open("wb") as out:
                    shutil.copyfileobj(src, out, length=1024 * 1024)
                return
            except Exception as e:
                last_error = e
                try:
                    if dest.exists():
                        dest.unlink()
                except Exception:
                    pass

        raise RuntimeError(
            f"ZIP 文件解压失败：{info.filename}; "
            f"可能原因：密码错误、AES加密不支持、压缩算法不支持、文件损坏。"
            f"原始错误：{last_error}"
        )

    def make_zip_alias_for_external_tool(self, source_path: Path) -> tuple[Path, bool]:
        """
        专用隐藏 ZIP 逻辑：

        66_1.mp4 / 66.mp4 如果已被识别为 hidden_zip：
        - 不复制
        - 不硬链接
        - 直接把原文件改名为 .zip
        - WinRAR 解压成功后删除 .zip
        - 不恢复原名

        返回：
        - alias_path: 改名后的 .zip 文件路径
        - need_delete: WinRAR 解压成功后是否删除
        """
        if source_path.name.lower().endswith(".zip"):
            return source_path, True

        alias_path = source_path.with_name(source_path.name + ".zip")

        if alias_path.exists():
            raise RuntimeError(f"无法改名隐藏 ZIP，目标文件已存在：{alias_path}")

        source_path.rename(alias_path)
        self.log(f"[隐藏ZIP改名] {source_path} -> {alias_path}")

        return alias_path, True

    def extract_by_winrar_to_dir(
        self,
        archive_name: str,
        fmt: str,
        extract_dir: Path,
        source_path: Path | None,
        source_data: bytes | None,
    ) -> None:
        if not self.winrar_path:
            raise RuntimeError("找不到 WinRAR / RAR / UnRAR。请安装 WinRAR，或在 WINRAR_PATH 中指定路径。")

        temp_source: Path | None = None
        alias_path: Path | None = None
        alias_need_delete = False
        success = False

        try:
            if source_data is not None:
                if self.temp_root is None:
                    raise RuntimeError("临时工作目录尚未初始化")
                temp_name = safe_rel_path(archive_name).name
                if fmt == "hidden_zip" and not temp_name.lower().endswith(".zip"):
                    temp_name += ".zip"

                temp_source = self.temp_root / f"winrar_src_{abs(hash(archive_name)) & 0xffffffff:x}_{temp_name}"
                temp_source.parent.mkdir(parents=True, exist_ok=True)
                if not CUSTOM_TEMP_IS_RAMDISK:
                    self.log("[提示] 外部工具需要临时源文件，此处会产生一次额外写入")
                temp_source.write_bytes(source_data)
                self.estimated_temp_write_bytes += len(source_data)
                self.log(f"[外部工具临时源] size={format_size(len(source_data))} path={temp_source}")
                archive_path = temp_source

            elif source_path is not None:
                if fmt == "hidden_zip":
                    alias_path, alias_need_delete = self.make_zip_alias_for_external_tool(source_path)
                    archive_path = alias_path
                    self.log(f"[WinRAR隐藏ZIP改名] {source_path} -> {archive_path}")
                else:
                    archive_path = source_path
            else:
                raise RuntimeError("WinRAR 来源为空")

            self.run_winrar_extract(archive_path, extract_dir)
            success = True

        finally:
            if success and alias_need_delete and alias_path is not None:
                try:
                    if alias_path.exists():
                        alias_path.unlink()
                        self.log(f"[隐藏ZIP解压成功后删除] {alias_path}")
                except Exception as e:
                    self.log(f"[隐藏ZIP删除失败] {alias_path}，原因：{e}")

            if temp_source is not None:
                try:
                    temp_source.unlink()
                except Exception:
                    pass

            gc.collect()

    def run_winrar_extract(self, archive_path: Path, extract_dir: Path) -> None:
        extract_dir.mkdir(parents=True, exist_ok=True)

        candidates: list[str | None] = [None]
        for p in self.passwords:
            if p not in candidates:
                candidates.append(p)

        errors: list[str] = []

        for pwd in candidates:
            self.checkpoint()
            cmd = [
                self.winrar_path,
                "x",
                "-y",
                "-o+",
            ]

            if pwd is None:
                cmd.append("-p-")
            else:
                cmd.append(f"-p{pwd}")

            cmd.append(str(archive_path))
            cmd.append(str(extract_dir) + os.sep)

            self.detail(f"[WinRAR尝试] 文件={archive_path.name} 密码={'无密码' if pwd is None else '已提供'}")

            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )

            if proc.returncode == 0:
                self.log(f"[WinRAR成功] {archive_path.name}")
                return

            msg = (proc.stderr or proc.stdout or "").strip() or f"WinRAR return code: {proc.returncode}"
            errors.append(f"pwd={'无密码' if pwd is None else '已提供'}: {msg[:1200]}")

        joined = "\n".join(errors[-5:])
        raise RuntimeError(f"WinRAR 解压失败。\n{joined}")

    def extract_by_7z_to_dir(
        self,
        archive_name: str,
        fmt: str,
        extract_dir: Path,
        source_path: Path | None,
        source_data: bytes | None,
    ) -> None:
        if not self.sevenzip_path:
            raise RuntimeError("找不到 7-Zip 命令行工具。请安装 7-Zip，或用 --sevenzip 指定 7z.exe 路径。")

        temp_source: Path | None = None
        temp_volume_dir: Path | None = None
        alias_path: Path | None = None
        alias_need_delete = False

        try:
            if fmt == "split" and source_path is not None and is_first_split_volume(source_path):
                volumes = collect_volume_set(source_path)
                total_size = volume_set_total_size(volumes)

                if should_use_memory(total_size, "split_volume", self.quiet):
                    if self.temp_root is None:
                        raise RuntimeError("临时工作目录尚未初始化")
                    self.log(f"[分卷RAM临时处理] volumes={len(volumes)} total={format_size(total_size)}")
                    temp_volume_dir = self.temp_root / f"volumes_{abs(hash(str(source_path))) & 0xffffffff:x}"
                    if temp_volume_dir.exists():
                        shutil.rmtree(temp_volume_dir, ignore_errors=True)
                    archive_path = materialize_volume_set_from_paths(volumes, temp_volume_dir)
                else:
                    self.log(f"[分卷直接解压] total={format_size(total_size)} name={source_path.name}")
                    archive_path = source_path

            elif source_data is not None:
                if self.temp_root is None:
                    raise RuntimeError("临时工作目录尚未初始化")
                temp_name = safe_rel_path(archive_name).name
                if fmt == "hidden_zip" and not temp_name.lower().endswith(".zip"):
                    temp_name += ".zip"
                temp_source = self.temp_root / f"src_{abs(hash(archive_name)) & 0xffffffff:x}_{temp_name}"
                temp_source.parent.mkdir(parents=True, exist_ok=True)
                if not CUSTOM_TEMP_IS_RAMDISK:
                    self.log("[提示] 外部工具需要临时源文件，此处会产生一次额外写入")
                temp_source.write_bytes(source_data)
                self.estimated_temp_write_bytes += len(source_data)
                self.log(f"[外部工具临时源] size={format_size(len(source_data))} path={temp_source}")
                archive_path = temp_source

            elif source_path is not None:
                if fmt == "hidden_zip":
                    alias_path, alias_need_delete = self.make_zip_alias_for_external_tool(source_path)
                    archive_path = alias_path
                    self.log(f"[7z隐藏ZIP别名] {source_path.name} -> {archive_path.name}")
                else:
                    archive_path = source_path
            else:
                raise RuntimeError("7z 来源为空")

            self.run_7z_extract(archive_path, extract_dir)

        finally:
            if alias_need_delete and alias_path is not None:
                try:
                    alias_path.unlink()
                except Exception:
                    pass

            if temp_source is not None:
                try:
                    temp_source.unlink()
                except Exception:
                    pass

            if temp_volume_dir is not None:
                shutil.rmtree(temp_volume_dir, ignore_errors=True)

            gc.collect()

    def run_7z_extract(self, archive_path: Path, extract_dir: Path) -> None:
        candidates: list[str | None] = [None]
        for p in self.passwords:
            if p not in candidates:
                candidates.append(p)

        errors: list[str] = []

        for pwd in candidates:
            self.checkpoint()
            cmd = [
                self.sevenzip_path or "7z",
                "x",
                str(archive_path),
                f"-o{extract_dir}",
                "-y",
                "-aoa",
                "-bsp1",
            ]

            if pwd is not None:
                cmd.append(f"-p{pwd}")

            self.detail(f"[7z尝试] 文件={archive_path.name} 密码={'无密码' if pwd is None else '已提供'}")

            proc = subprocess.run(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                errors="replace",
            )

            if proc.returncode == 0:
                self.log(f"[7z成功] {archive_path.name}")
                return

            msg = (proc.stderr or proc.stdout or "").strip() or f"7z return code: {proc.returncode}"
            errors.append(f"pwd={'无密码' if pwd is None else '已提供'}: {msg[:1200]}")

        joined = "\n".join(errors[-5:])
        raise RuntimeError(f"7z 解压失败，可能是密码错误、缺少分卷或格式不支持。\n{joined}")


# ============================================================
# 参数处理
# ============================================================

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="递归解压 ZIP / RAR / 7Z，支持嵌套、分卷、加密、隐藏ZIP。"
    )

    parser.add_argument(
        "--gui",
        action="store_true",
        help="启动最简单的 Windows 图形界面。",
    )

    parser.add_argument(
        "inputs",
        nargs="*",
        help="输入文件或文件夹。不填则默认扫描脚本所在文件夹。",
    )

    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="可选：指定统一输出目录。不填则输出到源压缩包同文件夹。",
    )

    parser.add_argument(
        "-p",
        "--password",
        action="append",
        default=[],
        help="临时增加候选密码，可重复传入。",
    )

    parser.add_argument(
        "--password-file",
        default=None,
        help="密码文件，每行一个密码。",
    )

    parser.add_argument(
        "--ask-password",
        action="store_true",
        help="运行时手动输入一个密码，输入内容不回显。",
    )

    parser.add_argument(
        "--sevenzip",
        default=None,
        help=r"手动指定 7z.exe 路径。例如：C:\Program Files\7-Zip\7z.exe",
    )

    parser.add_argument(
        "--winrar",
        default=None,
        help=r"手动指定 WinRAR.exe 路径。例如：C:\Program Files\WinRAR\WinRAR.exe",
    )

    parser.add_argument(
        "--quiet",
        action="store_true",
        help="减少日志输出。",
    )

    parser.add_argument(
        "--temp-mode",
        choices=["same_volume", "custom", "system"],
        default=TEMP_MODE,
        help="临时目录模式：same_volume 默认同输出目录同盘；custom 使用 --temp-dir；system 使用系统 Temp。",
    )

    parser.add_argument(
        "--temp-dir",
        default=CUSTOM_TEMP_DIR,
        help="自定义临时目录，例如机械硬盘或 RAM Disk。需配合 --temp-mode custom。",
    )

    parser.add_argument(
        "--temp-is-ramdisk",
        action="store_true",
        default=CUSTOM_TEMP_IS_RAMDISK,
        help="声明自定义临时目录是 RAM Disk，允许分卷材料化到临时目录。",
    )

    parser.add_argument(
        "--memory-ratio",
        type=float,
        default=MEMORY_RATIO,
        help="允许使用的当前可用内存比例，默认 0.80。",
    )

    parser.add_argument(
        "--max-memory-object-gb",
        type=float,
        default=MAX_MEMORY_OBJECT_BYTES / (1024 ** 3),
        help="单个内存对象最大 GB，默认 4。",
    )

    parser.add_argument(
        "--failed-archive-mode",
        choices=["log_only", "copy", "hardlink_or_copy"],
        default=FAILED_ARCHIVE_MODE,
        help="失败压缩包处理模式，默认 log_only。",
    )

    parser.add_argument(
        "--keep-source",
        action="store_true",
        help="回退旧行为：成功后保留原始根压缩包，不立即删除。",
    )

    return parser.parse_args()


def load_passwords(args: argparse.Namespace) -> list[str]:
    passwords: list[str] = []

    for p in PASSWORDS:
        if p and p not in passwords:
            passwords.append(p)

    for p in args.password or []:
        if p and p not in passwords:
            passwords.append(p)

    if args.password_file:
        path = Path(args.password_file)
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line and line not in passwords:
                    passwords.append(line)

    if args.ask_password:
        p = getpass.getpass("请输入压缩包密码：")
        if p and p not in passwords:
            passwords.append(p)

    return passwords


def resolve_inputs(args: argparse.Namespace) -> list[Path]:
    if args.inputs:
        return [Path(x) for x in args.inputs]

    if DEFAULT_SCAN_SCRIPT_FOLDER:
        try:
            return [Path(__file__).resolve().parent]
        except Exception:
            return [Path.cwd()]

    return [Path.cwd()]


def main() -> int:
    global _ACTIVE_UNPACKER
    global TEMP_MODE, CUSTOM_TEMP_DIR, CUSTOM_TEMP_IS_RAMDISK
    global MEMORY_RATIO, MAX_MEMORY_OBJECT_BYTES, FAILED_ARCHIVE_MODE
    global DELETE_ROOT_ARCHIVES_AFTER_SUCCESS
    global AUTO_RAMDISK_SELECTED_DIR

    install_exit_cleanup_handlers()

    args = parse_args()

    if args.gui:
        import gui as gui_module

        gui_module.main()
        return 0

    TEMP_MODE = args.temp_mode
    CUSTOM_TEMP_DIR = args.temp_dir
    CUSTOM_TEMP_IS_RAMDISK = bool(args.temp_is_ramdisk)
    MEMORY_RATIO = max(0.01, min(float(args.memory_ratio), 0.95))
    MAX_MEMORY_OBJECT_BYTES = max(1, int(float(args.max_memory_object_gb) * 1024 ** 3))
    FAILED_ARCHIVE_MODE = args.failed_archive_mode
    DELETE_ROOT_ARCHIVES_AFTER_SUCCESS = not args.keep_source
    AUTO_RAMDISK_SELECTED_DIR = ""

    if has_manual_temp_args():
        log("[RAM Disk 自动] 已检测到手动临时目录参数，跳过自动 RAM Disk 选择", args.quiet)
    else:
        ramdisk_ok, ramdisk_reason = apply_auto_ramdisk_config(args.quiet)
        if not ramdisk_ok:
            go_on = ask_ignore_or_abort(
                "RAM Disk 自动启用失败",
                (
                    "未能自动启用 RAM Disk 临时目录。\n\n"
                    f"原因：{ramdisk_reason}\n\n"
                    "选择【是】继续运行：将使用普通 same_volume 临时目录，分卷不会进行 RAM 临时处理。\n"
                    "选择【否】终止程序：请先创建/挂载 RAM Disk 后再运行。"
                ),
            )
            if not go_on:
                raise SystemExit(1)

    passwords = load_passwords(args)
    inputs = resolve_inputs(args)
    output_override = Path(args.output).resolve() if args.output else None

    unpacker = NestedUnpacker(
        passwords=passwords,
        sevenzip_path=args.sevenzip,
        winrar_path=args.winrar,
        quiet=args.quiet,
        output_override=output_override,
    )
    _ACTIVE_UNPACKER = unpacker

    try:
        unpacker.run(inputs)
        return 0
    finally:
        release_memory_now()


if __name__ == "__main__":
    exit_code = 1

    try:
        exit_code = main()

    except KeyboardInterrupt:
        pass

    except SystemExit as e:
        raise e

    except Exception as e:
        go_on = ask_ignore_or_abort("程序异常", str(e))
        if not go_on:
            raise

    finally:
        release_memory_now()
        pause_if_needed()

    raise SystemExit(exit_code)
