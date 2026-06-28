from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from archive_detector import analyze_archive_candidate
from unpacker import NestedUnpacker, detect_format, is_non_first_volume, split_skip_reason


DEFAULT_PASSWORDS = ["孙笑川258"]


class PrintLogger:
    def log(self, message: str) -> None:
        print(message)


def default_output_dir(archive_path: Path) -> Path:
    name = archive_path.name
    for suffix in (".7z.001", ".zip.001", ".001", ".zip"):
        if name.lower().endswith(suffix):
            name = name[: -len(suffix)]
            break
    return Path.cwd() / "_split_mainflow_output" / name


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 5：分卷主流程测试脚本")
    parser.add_argument("archive", help="要测试的分卷入口或数据卷")
    parser.add_argument("output_dir", nargs="?", help="可选：输出目录")
    args = parser.parse_args()

    archive_path = Path(args.archive)
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(archive_path)

    info = analyze_archive_candidate(archive_path, passwords=DEFAULT_PASSWORDS)
    fmt = detect_format(archive_path.name, path=archive_path)

    print(f"[识别] {archive_path.name}")
    print(f"  detector_real_format={info.real_format}")
    print(f"  detector_engine_hint={info.engine_hint}")
    print(f"  mainflow_format={fmt}")
    print(f"  is_split={info.is_split}")
    print(f"  is_split_first_volume={info.is_split_first_volume}")
    print(f"  is_split_non_first_volume={info.is_split_non_first_volume}")

    if is_non_first_volume(archive_path):
        print(f"[跳过] {archive_path.name}：{split_skip_reason(archive_path)}")
        print("[结果] 跳过")
        return 0

    if fmt is None:
        print("[结果] 失败")
        print("[错误信息] 不是可识别分卷入口")
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / "._split_mainflow_cache"
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    unpacker = NestedUnpacker(
        passwords=DEFAULT_PASSWORDS,
        quiet=False,
        output_override=output_dir,
        logger=PrintLogger(),
    )
    unpacker.temp_root = work_dir

    try:
        unpacker.expand_archive(
            archive_name=archive_path.name,
            target_dir=output_dir,
            depth=0,
            source_path=archive_path,
            source_data=None,
            delete_source_after=False,
            root_output_dir=output_dir,
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
