from __future__ import annotations

import importlib
import sys
from pathlib import Path


sys.dont_write_bytecode = True


ROOT = Path(__file__).resolve().parent
RELEASE_EXE = ROOT / "release" / "多层嵌套压缩解压工具.exe"


def show_ok(message: str) -> None:
    print(f"[OK] {message}")


def show_missing(path: str) -> None:
    print(f"[缺失] {path}")


def show_fail(message: str) -> None:
    print(f"[失败] {message}")


def check_file(relative_path: str, required: bool = True) -> bool:
    path = ROOT / relative_path
    if path.is_file():
        show_ok(f"{relative_path} 存在")
        return True

    if required:
        show_missing(relative_path)
    else:
        print(f"[提示] {relative_path} 不存在")
    return not required


def check_import(module_name: str) -> object | None:
    try:
        module = importlib.import_module(module_name)
    except Exception as exc:
        show_fail(f"导入 {module_name} 失败：{exc}")
        return None

    show_ok(f"已导入 {module_name}")
    return module


def main() -> int:
    print("[发布检查]")
    print()

    checks_ok = True

    required_files = (
        "gui.py",
        "unpacker.py",
        "archive_detector.py",
        "archive_7z_engine.py",
        "logo.ico",
        "tools\\7z\\7z.exe",
        "tools\\7z\\7z.dll",
        "tools\\7z\\License.txt",
        "build_single_exe.bat",
        "docs\\V2升级规划.md",
        "docs\\打包说明.md",
        "docs\\使用说明.md",
    )

    for relative_path in required_files:
        checks_ok = check_file(relative_path) and checks_ok

    archive_detector = check_import("archive_detector")
    checks_ok = archive_detector is not None and checks_ok

    archive_7z_engine = check_import("archive_7z_engine")
    checks_ok = archive_7z_engine is not None and checks_ok

    if archive_7z_engine is not None:
        try:
            seven_zip = archive_7z_engine.find_7z_executable()
        except Exception as exc:
            show_fail(f"调用 find_7z_executable() 失败：{exc}")
            checks_ok = False
        else:
            if seven_zip:
                show_ok(f"7z 已找到：{seven_zip}")
            else:
                show_fail("7z 未找到")
                print("[建议] 本机未发现 7z，请补齐 tools\\7z\\7z.exe 和 tools\\7z\\7z.dll 作为随包兜底")
                checks_ok = False

    unpacker = check_import("unpacker")
    checks_ok = unpacker is not None and checks_ok

    if (ROOT / "release").is_dir():
        if RELEASE_EXE.is_file():
            show_ok(f"release\\{RELEASE_EXE.name} 存在")
            print("[提示] 已发现 release EXE，本阶段不重复打包，避免额外写入。")
        else:
            print(f"[提示] release 目录存在，但未发现 {RELEASE_EXE.name}")
    else:
        print("[提示] release 目录不存在，本阶段不自动打包。")

    missing_7z_files = [
        path
        for path in (
            "tools\\7z\\7z.exe",
            "tools\\7z\\7z.dll",
            "tools\\7z\\License.txt",
        )
        if not (ROOT / path).is_file()
    ]
    for path in missing_7z_files:
        print(f"[建议] 缺少随包兜底文件：{path}")

    print()
    if checks_ok:
        print("[结果] 轻量检查通过")
        return 0

    print("[结果] 轻量检查未通过")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
