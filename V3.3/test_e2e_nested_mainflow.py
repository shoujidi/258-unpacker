from __future__ import annotations

import argparse
import shutil
from pathlib import Path

from archive_detector import MAX_NESTED_DEPTH
from unpacker import NestedUnpacker


DEFAULT_PASSWORDS = ["孙笑川258"]


class PrintLogger:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def log(self, message: str) -> None:
        text = str(message)
        self.lines.append(text)
        print(text)


def default_output_dir(target: Path) -> Path:
    name = target.stem if target.is_file() and target.suffix else target.name
    return Path.cwd() / "_e2e_nested_output" / (name or "target")


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 7：11 层递归整合全流程测试")
    parser.add_argument("target", help="要测试的压缩包或目录")
    parser.add_argument("output_dir", nargs="?", help="可选：输出目录")
    args = parser.parse_args()

    target = Path(args.target)
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(target)

    print(f"[测试目标] {target}")
    print(f"[输出目录] {output_dir}")
    print(f"[最大层数] {MAX_NESTED_DEPTH}")

    if not target.exists():
        print("[结果] 失败")
        print("[错误信息] 测试目标不存在")
        return 1

    if output_dir.exists():
        shutil.rmtree(output_dir, ignore_errors=True)
    output_dir.mkdir(parents=True, exist_ok=True)

    logger = PrintLogger()
    unpacker = NestedUnpacker(
        passwords=DEFAULT_PASSWORDS,
        quiet=False,
        output_override=output_dir,
        logger=logger,
    )

    try:
        unpacker.run([target])
    except Exception as exc:
        print("[结果] 失败")
        print(f"[错误信息] {exc}")
        return 1
    finally:
        unpacker.close()

    print(f"[处理文件数] {unpacker.total_archives}")
    print(f"[成功解压数] {unpacker.total_archives - unpacker.total_failed}")
    print(f"[跳过文件数] {unpacker.total_skipped}")
    print(f"[失败数] {unpacker.total_failed}")
    print("[结果] 完成")

    return 0 if unpacker.total_failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
