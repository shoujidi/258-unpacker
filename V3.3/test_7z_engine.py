from __future__ import annotations

import argparse
from pathlib import Path

from archive_7z_engine import extract_with_7z_password_pool, find_7z_executable


DEFAULT_PASSWORDS = ["孙笑川258"]


def default_output_dir(archive_path: Path) -> Path:
    return Path.cwd() / "_7z_test_output" / archive_path.with_suffix("").name


def password_label(password: str | None) -> str:
    return "无密码" if password is None else password


def main() -> int:
    parser = argparse.ArgumentParser(description="阶段 2：7z 引擎测试脚本")
    parser.add_argument("archive", help="要测试解压的压缩包路径")
    parser.add_argument("output_dir", nargs="?", help="可选：输出目录")
    args = parser.parse_args()

    archive_path = Path(args.archive)
    output_dir = Path(args.output_dir) if args.output_dir else default_output_dir(archive_path)
    seven_zip = find_7z_executable()

    result = extract_with_7z_password_pool(archive_path, output_dir, DEFAULT_PASSWORDS)

    print(f"[7z路径] {seven_zip if seven_zip else '未找到'}")
    print(f"[压缩包] {archive_path}")
    print(f"[输出目录] {output_dir}")
    print(f"[结果] {'成功' if result.success else '失败'}")
    print(f"[使用密码] {password_label(result.used_password)}")
    print(f"[返回码] {result.return_code}")
    print(f"[错误信息] {result.error}")

    return 0 if result.success else 1


if __name__ == "__main__":
    raise SystemExit(main())
