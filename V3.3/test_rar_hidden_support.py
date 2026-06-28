from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.dont_write_bytecode = True

from archive_detector import analyze_archive_candidate, is_rar_magic
from unpacker import (
    detect_format,
    display_archive_type,
    is_non_first_volume,
    split_skip_reason,
)

RAR4_MAGIC = b"Rar!\x1a\x07\x00"


def write_bytes(path: Path, data: bytes = b"x") -> Path:
    path.write_bytes(data)
    return path


def assert_info(
    path: Path,
    *,
    real_format: str,
    is_archive: bool,
    first: bool = False,
    non_first: bool = False,
) -> None:
    info = analyze_archive_candidate(path)
    assert info.real_format == real_format, (path.name, info.real_format)
    assert info.is_archive is is_archive, (path.name, info.is_archive)
    assert info.is_split_first_volume is first, (path.name, info.is_split_first_volume)
    assert info.is_split_non_first_volume is non_first, (path.name, info.is_split_non_first_volume)


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="archive_entry_detect_") as tmp:
        root = Path(tmp)

        plain = write_bytes(root / "plain.rar", RAR4_MAGIC + b"tiny")
        assert is_rar_magic(RAR4_MAGIC)
        assert_info(plain, real_format="rar", is_archive=True)
        assert detect_format(plain.name, path=plain) == "rar"
        assert display_archive_type("rar") == "压缩包"

        new_first = write_bytes(root / "movie.part1.rar")
        new_second = write_bytes(root / "movie.part2.rar")
        assert_info(new_first, real_format="split_rar_new", is_archive=True, first=True)
        assert_info(new_second, real_format="split_rar_new", is_archive=False, non_first=True)
        assert detect_format(new_first.name, path=new_first) == "split_rar_new"
        assert is_non_first_volume(new_second)
        assert split_skip_reason(new_second) == "非入口分卷"
        assert display_archive_type("split_rar_new") == "分卷入口"

        old_first = write_bytes(root / "legacy.rar")
        old_second = write_bytes(root / "legacy.r00")
        assert_info(old_first, real_format="split_rar_old", is_archive=True, first=True)
        assert_info(old_second, real_format="split_rar_old", is_archive=False, non_first=True)
        assert detect_format(old_first.name, path=old_first) == "split_rar_old"
        assert is_non_first_volume(old_second)
        assert split_skip_reason(old_second) == "非入口分卷"
        assert display_archive_type("split_rar_old") == "分卷入口"

    print("OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
