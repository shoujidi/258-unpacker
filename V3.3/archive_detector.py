from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


MAX_NESTED_DEPTH = 11

MP4_EMBEDDED_ZIP_KEYWORD = "孙笑川"
ENABLE_MP4_EMBEDDED_ZIP_FAST_PATH = True
ENABLE_MP4_EMBEDDED_ZIP_FALLBACK = True

MP4_SCAN_CHUNK_SIZE = 8 * 1024 * 1024
MP4_SCAN_SIGNATURE = b"PK\x03\x04"


@dataclass
class ArchiveInfo:
    path: Path
    is_archive: bool
    real_format: str
    engine_hint: str
    is_split: bool = False
    is_split_first_volume: bool = False
    is_split_non_first_volume: bool = False
    is_mp4: bool = False
    is_mp4_embedded_zip: bool = False
    embedded_zip_offset: int | None = None
    skip_reason: str = ""
    note: str = ""


def read_magic(path: Path, size: int = 512) -> bytes:
    with path.open("rb") as f:
        return f.read(size)


def is_zip_magic(data: bytes) -> bool:
    return data.startswith((b"PK\x03\x04", b"PK\x05\x06", b"PK\x07\x08"))


def is_7z_magic(data: bytes) -> bool:
    return data.startswith(b"7z\xbc\xaf\x27\x1c")


def is_rar_magic(data: bytes) -> bool:
    return data.startswith(b"Rar!\x1a\x07\x00") or data.startswith(b"Rar!\x1a\x07\x01\x00")


def is_gz_magic(data: bytes) -> bool:
    return data.startswith(b"\x1f\x8b")


def is_bz2_magic(data: bytes) -> bool:
    return data.startswith(b"BZh")


def is_xz_magic(data: bytes) -> bool:
    return data.startswith(b"\xfd7zXZ\x00")


def is_probably_tar(path: Path) -> bool:
    if path.name.lower().endswith(".tar"):
        return True

    try:
        with path.open("rb") as f:
            f.seek(257)
            return f.read(5) == b"ustar"
    except OSError:
        return False


def is_probably_mp4(data: bytes) -> bool:
    return b"ftyp" in data[:32]


def password_pool_contains_keyword(
    passwords: list[str],
    keyword: str = MP4_EMBEDDED_ZIP_KEYWORD,
) -> bool:
    return any(keyword in password for password in passwords)


def should_scan_mp4_for_embedded_zip(passwords: list[str]) -> bool:
    if ENABLE_MP4_EMBEDDED_ZIP_FAST_PATH and password_pool_contains_keyword(passwords):
        return True
    return ENABLE_MP4_EMBEDDED_ZIP_FALLBACK


def get_mp4_scan_note(passwords: list[str]) -> str:
    if ENABLE_MP4_EMBEDDED_ZIP_FAST_PATH and password_pool_contains_keyword(passwords):
        return "[MP4扫描] 密码池包含“孙笑川”，高优先级扫描"
    if ENABLE_MP4_EMBEDDED_ZIP_FALLBACK:
        return "[MP4扫描] 密码池不包含“孙笑川”，兜底扫描"
    return "[MP4扫描] 密码池不包含“孙笑川”，跳过扫描"


def find_signature_in_file(
    path: Path,
    signature: bytes = MP4_SCAN_SIGNATURE,
    chunk_size: int = MP4_SCAN_CHUNK_SIZE,
) -> int | None:
    if not signature:
        return None
    if chunk_size <= len(signature):
        raise ValueError("chunk_size must be larger than signature length")

    overlap_size = max(len(signature) - 1, 0)
    previous_tail = b""
    offset = 0

    with path.open("rb") as f:
        while True:
            chunk = f.read(chunk_size)
            if not chunk:
                return None

            data = previous_tail + chunk
            found_at = data.find(signature)
            if found_at >= 0:
                return offset - len(previous_tail) + found_at

            previous_tail = data[-overlap_size:] if overlap_size else b""
            offset += len(chunk)


def detect_split_archive(path: Path) -> tuple[bool, bool, str]:
    name = path.name.lower()

    match = re.search(r"\.part0*(\d+)\.rar$", name)
    if match:
        return True, int(match.group(1)) == 1, "split_rar_new"

    if re.search(r"\.r\d{2,}$", name):
        return True, False, "split_rar_old"

    if name.endswith(".rar") and path.with_suffix(".r00").exists():
        return True, True, "split_rar_old"

    match = re.search(r"\.7z\.(\d{3})$", name)
    if match:
        return True, match.group(1) == "001", "split_7z"

    match = re.search(r"\.zip\.(\d{3})$", name)
    if match:
        return True, match.group(1) == "001", "split_zip"

    match = re.search(r"\.(\d{3})$", name)
    if match:
        return True, match.group(1) == "001", "split_numeric"

    if re.search(r"\.z\d{2}$", name):
        return True, False, "split_zip_legacy"

    if name.endswith(".zip") and path.with_suffix(".z01").exists():
        return True, True, "split_zip_legacy"

    return False, False, ""


def analyze_archive_candidate(path: Path, passwords: list[str] | None = None) -> ArchiveInfo:
    path = Path(path)
    passwords = list(passwords or [])

    if not path.exists():
        return ArchiveInfo(
            path=path,
            is_archive=False,
            real_format="unknown",
            engine_hint="skip",
            skip_reason="路径不存在",
        )

    if not path.is_file():
        return ArchiveInfo(
            path=path,
            is_archive=False,
            real_format="unknown",
            engine_hint="skip",
            skip_reason="不是文件",
        )

    is_split, is_first_volume, split_type = detect_split_archive(path)
    if is_split and not is_first_volume:
        return ArchiveInfo(
            path=path,
            is_archive=False,
            real_format=split_type,
            engine_hint="skip",
            is_split=True,
            is_split_non_first_volume=True,
            skip_reason="非第一卷分卷，跳过",
        )

    if is_split and is_first_volume:
        return ArchiveInfo(
            path=path,
            is_archive=True,
            real_format=split_type,
            engine_hint="seven_zip",
            is_split=True,
            is_split_first_volume=True,
            note="第一卷分卷，作为 7z 入口候选",
        )

    try:
        magic = read_magic(path)
    except OSError as exc:
        return ArchiveInfo(
            path=path,
            is_archive=False,
            real_format="unknown",
            engine_hint="skip",
            skip_reason=f"无法读取文件头：{exc}",
        )

    lower_name = path.name.lower()
    if lower_name.endswith(".mp4"):
        scan_note = get_mp4_scan_note(passwords)
        looks_like_mp4 = is_probably_mp4(magic)
        embedded_zip_offset = (
            find_signature_in_file(path) if should_scan_mp4_for_embedded_zip(passwords) else None
        )
        if embedded_zip_offset is not None:
            return ArchiveInfo(
                path=path,
                is_archive=True,
                real_format="mp4_embedded_zip",
                engine_hint="carve_pyzip_mp4_embedded_zip",
                is_mp4=looks_like_mp4,
                is_mp4_embedded_zip=True,
                embedded_zip_offset=embedded_zip_offset,
                note=scan_note,
            )

        return ArchiveInfo(
            path=path,
            is_archive=False,
            real_format="mp4",
            engine_hint="skip",
            is_mp4=looks_like_mp4,
            skip_reason="真实 MP4 或未发现内嵌 ZIP",
            note=scan_note,
        )

    if lower_name.endswith(".tar.gz"):
        return ArchiveInfo(path=path, is_archive=True, real_format="tar_gz", engine_hint="seven_zip")

    if lower_name.endswith(".tgz"):
        return ArchiveInfo(path=path, is_archive=True, real_format="tgz", engine_hint="seven_zip")

    if lower_name.endswith(".tar.bz2"):
        return ArchiveInfo(path=path, is_archive=True, real_format="tar_bz2", engine_hint="seven_zip")

    if lower_name.endswith(".tar.xz"):
        return ArchiveInfo(path=path, is_archive=True, real_format="tar_xz", engine_hint="seven_zip")

    if is_zip_magic(magic):
        return ArchiveInfo(path=path, is_archive=True, real_format="zip", engine_hint="python_zip")

    if is_7z_magic(magic):
        return ArchiveInfo(path=path, is_archive=True, real_format="7z", engine_hint="seven_zip")

    if is_rar_magic(magic):
        return ArchiveInfo(path=path, is_archive=True, real_format="rar", engine_hint="seven_zip")

    if is_gz_magic(magic):
        return ArchiveInfo(path=path, is_archive=True, real_format="gz", engine_hint="seven_zip")

    if is_bz2_magic(magic):
        return ArchiveInfo(path=path, is_archive=True, real_format="bz2", engine_hint="seven_zip")

    if is_xz_magic(magic):
        return ArchiveInfo(path=path, is_archive=True, real_format="xz", engine_hint="seven_zip")

    if is_probably_tar(path):
        return ArchiveInfo(path=path, is_archive=True, real_format="tar", engine_hint="seven_zip")

    if lower_name.endswith(".rar"):
        return ArchiveInfo(path=path, is_archive=True, real_format="rar", engine_hint="seven_zip")

    return ArchiveInfo(
        path=path,
        is_archive=False,
        real_format="unknown",
        engine_hint="skip",
        skip_reason="非压缩普通文件或未知格式",
    )
