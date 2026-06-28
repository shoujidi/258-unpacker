from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from archive_detector import analyze_archive_candidate
from unpacker import NestedUnpacker


DEFAULT_PASSWORDS = ["孙笑川258"]


class PrintLogger:
    def log(self, message: str) -> None:
        print(message)


def default_output_dir(archive_path: Path) -> Path:
    name = archive_path.stem if archive_path.suffix else archive_path.name
    return Path.cwd() / "_zip_fallback_output" / name


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 3：ZIP 7z 兜底测试脚本")
    parser.add_argument("archive", help="要测试的 ZIP 候选文件")
    parser.add_argument("output_dir", nargs="?", help="可选：输出目录")
    args = parser.parse_args()

    archive_path = Path(args.archive)
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(archive_path)

    info = analyze_archive_candidate(archive_path, passwords=DEFAULT_PASSWORDS)
    print(f"[识别] {archive_path.name}")
    print(f"  real_format={info.real_format}")
    print(f"  engine_hint={info.engine_hint}")
    print(f"  is_archive={info.is_archive}")

    if not (info.is_archive and info.real_format == "zip" and info.engine_hint == "python_zip"):
        print("[结果] 失败")
        print("[错误信息] 不是本阶段处理的 ZIP 候选文件")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / "._zip_fallback_cache"

    unpacker = NestedUnpacker(
        passwords=DEFAULT_PASSWORDS,
        quiet=False,
        output_override=output_dir,
        logger=PrintLogger(),
    )
    unpacker.temp_root = work_dir
    work_dir.mkdir(parents=True, exist_ok=True)

    try:
        unpacker.extract_zip_with_7z_fallback(
            archive_name=archive_path.name,
            extract_dir=output_dir,
            source_path=archive_path,
            source_data=None,
        )
        print("[结果] 成功")
        return 0
    except Exception as exc:
        print("[结果] 失败")
        print(f"[错误信息] {exc}")
        return 1
    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        finally:
            unpacker.temp_root = None
            unpacker.close()


if __name__ == "__main__":
    raise SystemExit(main())
