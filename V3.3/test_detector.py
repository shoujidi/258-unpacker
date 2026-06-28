from __future__ import annotations

import argparse
from pathlib import Path

from archive_detector import ArchiveInfo, analyze_archive_candidate


TEST_PASSWORDS = ["孙笑川258"]


def iter_files(root: Path) -> list[Path]:
    if root.is_file():
        return [root]
    return sorted((p for p in root.rglob("*") if p.is_file()), key=lambda p: str(p).lower())


def print_archive_info(info: ArchiveInfo, base_dir: Path | None = None) -> None:
    display_path = info.path
    if base_dir is not None:
        try:
            display_path = info.path.relative_to(base_dir)
        except ValueError:
            pass

    print(f"[识别] {display_path}")
    print(f"  real_format={info.real_format}")
    print(f"  engine_hint={info.engine_hint}")
    print(f"  is_archive={info.is_archive}")

    if info.is_split:
        print(f"  is_split={info.is_split}")
        print(f"  is_split_first_volume={info.is_split_first_volume}")
        print(f"  is_split_non_first_volume={info.is_split_non_first_volume}")

    if info.is_mp4:
        print(f"  is_mp4={info.is_mp4}")

    if info.is_mp4_embedded_zip:
        print(f"  is_mp4_embedded_zip={info.is_mp4_embedded_zip}")

    if info.embedded_zip_offset is not None:
        print(f"  embedded_zip_offset={info.embedded_zip_offset}")

    if info.skip_reason:
        print(f"  skip_reason={info.skip_reason}")

    if info.note:
        print(f"  note={info.note}")

    print()


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 1：文件识别器测试脚本")
    parser.add_argument("path", help="要遍历识别的文件或目录")
    args = parser.parse_args()

    root = Path(args.path)
    if not root.exists():
        print(f"路径不存在：{root}")
        return 1

    base_dir = root if root.is_dir() else root.parent
    for path in iter_files(root):
        info = analyze_archive_candidate(path, passwords=TEST_PASSWORDS)
        print_archive_info(info, base_dir=base_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
