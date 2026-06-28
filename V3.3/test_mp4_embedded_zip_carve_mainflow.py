from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from archive_detector import analyze_archive_candidate
from unpacker import NestedUnpacker, PASSWORDS, detect_format


class PrintLogger:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def log(self, message: str) -> None:
        self.messages.append(message)
        print(message)


def default_output_dir(archive_path: Path) -> Path:
    return Path.cwd() / "_mp4_embedded_zip_carve_output" / archive_path.stem


def cache_dir() -> Path:
    return Path.cwd() / "cache" / "mp4_embedded_zip"


def existing_temp_zips() -> set[Path]:
    root = cache_dir()
    if not root.exists():
        return set()
    return {path.resolve() for path in root.glob("*.zip") if path.is_file()}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="测试 V3 主流程是否使用 carve + Python zipfile 处理 MP4 内嵌 ZIP"
    )
    parser.add_argument("archive", help="要测试的 MP4 文件")
    parser.add_argument("output_dir", nargs="?", help="可选：输出目录")
    args = parser.parse_args()

    archive_path = Path(args.archive)
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(archive_path)
    alias_path = archive_path.with_name(archive_path.name + ".zip")

    info = analyze_archive_candidate(archive_path, passwords=PASSWORDS)
    fmt = detect_format(archive_path.name, path=archive_path)

    print(f"[识别] {archive_path.name}")
    print(f"  detector_real_format={info.real_format}")
    print(f"  detector_engine_hint={info.engine_hint}")
    print(f"  mainflow_format={fmt}")
    print(f"  is_mp4_embedded_zip={info.is_mp4_embedded_zip}")
    print(f"  embedded_zip_offset={info.embedded_zip_offset}")

    if fmt != "mp4_embedded_zip":
        print("[结果] 跳过")
        print("[说明] 未识别为 MP4 内嵌 ZIP")
        return 0

    output_dir.mkdir(parents=True, exist_ok=True)
    work_dir = output_dir / "._mp4_embedded_zip_carve_cache"
    if work_dir.exists():
        shutil.rmtree(work_dir, ignore_errors=True)
    work_dir.mkdir(parents=True, exist_ok=True)

    logger = PrintLogger()
    unpacker = NestedUnpacker(
        passwords=PASSWORDS,
        quiet=False,
        output_override=output_dir,
        logger=logger,
    )
    unpacker.temp_root = work_dir
    flow_log_start = len(logger.messages)

    before_cache = existing_temp_zips()

    try:
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
            success = True
            print("[结果] 成功")
        except Exception as exc:
            success = False
            print("[结果] 失败")
            print(f"[错误信息] {exc}")

        after_cache = existing_temp_zips()
        new_cache_files = sorted(after_cache - before_cache, key=lambda path: str(path).lower())

        print(f"[文件状态] mp4_exists={archive_path.exists()}")
        print(f"[文件状态] zip_alias_exists={alias_path.exists()}")
        print(f"[缓存状态] new_temp_zip_count={len(new_cache_files)}")
        for path in new_cache_files:
            print(f"[缓存状态] retained_temp_zip={path}")

        if not archive_path.exists():
            print("[验收] 失败：原 MP4 不存在")
            return 1
        if alias_path.exists():
            print("[验收] 失败：发现旧流程 .zip 别名")
            return 1
        flow_messages = logger.messages[flow_log_start:]
        if not any("[临时ZIP]" in message for message in flow_messages):
            print("[验收] 失败：未发现临时 ZIP 生成日志")
            return 1
        if success and new_cache_files:
            print("[验收] 失败：成功后临时 ZIP 未删除")
            return 1
        if not success and not new_cache_files:
            print("[验收] 失败：失败后未保留临时 ZIP")
            return 1

        print("[验收] 通过")
        return 0
    finally:
        try:
            shutil.rmtree(work_dir, ignore_errors=True)
        finally:
            unpacker.temp_root = None
            unpacker.close()


if __name__ == "__main__":
    raise SystemExit(main())
