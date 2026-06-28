#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from __future__ import annotations

import ctypes
import glob
import os
import shutil
import subprocess
import sys
from pathlib import Path


APP_NAME = "\u591a\u5c42\u5d4c\u5957\u538b\u7f29\u89e3\u538b\u5de5\u5177"


def hide_folder_if_exists(path: Path) -> None:
    if os.name != "nt":
        return
    try:
        if path.exists() and path.is_dir():
            ctypes.windll.kernel32.SetFileAttributesW(str(path), 0x02)
    except Exception:
        pass


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path, ignore_errors=True)
    elif path.exists():
        try:
            path.unlink()
        except OSError:
            pass


def ensure_pyinstaller_available() -> bool:
    result = subprocess.run(
        [sys.executable, "-m", "PyInstaller", "--version"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    if result.returncode == 0:
        return True

    print("未检测到 PyInstaller，无法打包。")
    print("请先运行：")
    print(f'"{sys.executable}" -m pip install pyinstaller')
    print()
    return False


def main() -> int:
    root = Path(__file__).resolve().parent
    os.chdir(root)

    if not ensure_pyinstaller_available():
        return 1

    remove_path(root / "build")
    remove_path(root / "dist")
    remove_path(root / "release")
    for spec in glob.glob("*.spec"):
        remove_path(root / spec)

    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name",
        APP_NAME,
        "--icon",
        "logo.ico",
        "--add-data",
        "logo.ico;.",
        "--add-binary",
        "tools\\7z\\7z.exe;tools\\7z",
        "--add-binary",
        "tools\\7z\\7z.dll;tools\\7z",
        "--add-data",
        "tools\\7z\\License.txt;tools\\7z",
        "--runtime-tmpdir",
        ".runtime_cache",
        "gui.py",
    ]
    result = subprocess.run(cmd)
    if result.returncode != 0:
        print()
        print("PyInstaller 打包失败，请查看上方输出。")
        return result.returncode

    release_dir = root / "release"
    release_dir.mkdir(exist_ok=True)

    src = root / "dist" / f"{APP_NAME}.exe"
    dst = release_dir / f"{APP_NAME}.exe"
    shutil.copy2(src, dst)

    print()
    print("\u6253\u5305\u5b8c\u6210\uff1a")
    print(dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
