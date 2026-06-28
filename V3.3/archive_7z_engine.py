from __future__ import annotations

import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


SEVEN_ZIP_PATH = ""
SEVEN_ZIP_TIMEOUT_SECONDS = 60 * 60

MISSING_7Z_MESSAGE = "未找到 7z.exe，请放入 tools\\7z\\7z.exe 和 7z.dll"


@dataclass
class SevenZipResult:
    success: bool
    used_password: str | None
    return_code: int
    stdout: str
    stderr: str
    error: str = ""


def resource_path(relative_path: str) -> Path:
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / relative_path
    return Path(__file__).resolve().parent / relative_path


def _existing_executable(path: Path) -> Path | None:
    try:
        if path.is_file():
            return path
    except OSError:
        return None
    return None


def find_7z_executable() -> Path | None:
    if SEVEN_ZIP_PATH:
        found = _existing_executable(Path(SEVEN_ZIP_PATH))
        if found:
            return found

    for candidate in (
        Path(r"C:\Program Files\7-Zip\7z.exe"),
        Path(r"C:\Program Files (x86)\7-Zip\7z.exe"),
    ):
        found = _existing_executable(candidate)
        if found:
            return found

    for command in ("7z.exe", "7za.exe"):
        resolved = shutil.which(command)
        if resolved:
            return Path(resolved)

    for relative_path in (
        "tools/7z/7z.exe",
        "tools/7z/7za.exe",
    ):
        found = _existing_executable(resource_path(relative_path))
        if found:
            return found

    project_dir = Path(__file__).resolve().parent
    for relative_path in (
        Path("tools") / "7z" / "7z.exe",
        Path("tools") / "7z" / "7za.exe",
    ):
        found = _existing_executable(project_dir / relative_path)
        if found:
            return found

    return None


def build_7z_extract_command(
    seven_zip: Path,
    archive_path: Path,
    output_dir: Path,
    password: str | None,
) -> list[str]:
    args = [
        str(seven_zip),
        "x",
        "-y",
    ]

    if password is not None:
        args.append(f"-p{password}")

    args.extend(
        [
            f"-o{output_dir}",
            str(archive_path),
        ]
    )
    return args


def _decode_process_output(data: bytes | str | None) -> str:
    if data is None:
        return ""
    if isinstance(data, str):
        return data
    return data.decode("utf-8", errors="replace")


def run_7z_extract_once(
    archive_path: Path,
    output_dir: Path,
    password: str | None = None,
) -> SevenZipResult:
    seven_zip = find_7z_executable()
    if seven_zip is None:
        return SevenZipResult(
            success=False,
            used_password=password,
            return_code=-1,
            stdout="",
            stderr="",
            error=MISSING_7Z_MESSAGE,
        )

    archive_path = Path(archive_path)
    output_dir = Path(output_dir)

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        return SevenZipResult(
            success=False,
            used_password=password,
            return_code=-1,
            stdout="",
            stderr="",
            error=f"无法创建输出目录：{exc}",
        )

    command = build_7z_extract_command(seven_zip, archive_path, output_dir, password)

    try:
        proc = subprocess.run(
            command,
            shell=False,
            capture_output=True,
            timeout=SEVEN_ZIP_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired as exc:
        return SevenZipResult(
            success=False,
            used_password=password,
            return_code=-1,
            stdout=_decode_process_output(exc.stdout),
            stderr=_decode_process_output(exc.stderr),
            error=f"7z 解压超时：{SEVEN_ZIP_TIMEOUT_SECONDS} 秒",
        )
    except OSError as exc:
        return SevenZipResult(
            success=False,
            used_password=password,
            return_code=-1,
            stdout="",
            stderr="",
            error=f"7z 执行失败：{exc}",
        )

    stdout = _decode_process_output(proc.stdout)
    stderr = _decode_process_output(proc.stderr)
    success = proc.returncode == 0
    error = "" if success else (stderr.strip() or stdout.strip() or f"7z return code: {proc.returncode}")

    return SevenZipResult(
        success=success,
        used_password=password,
        return_code=proc.returncode,
        stdout=stdout,
        stderr=stderr,
        error=error,
    )


def normalize_password_pool(passwords: list[str] | None) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()

    for password in passwords or []:
        cleaned = password.strip()
        if not cleaned or cleaned in seen:
            continue
        normalized.append(cleaned)
        seen.add(cleaned)

    return normalized


def extract_with_7z_password_pool(
    archive_path: Path,
    output_dir: Path,
    passwords: list[str] | None = None,
) -> SevenZipResult:
    normalized_passwords = normalize_password_pool(passwords)
    attempts: list[str | None] = [None, *normalized_passwords]
    errors: list[str] = []
    last_result: SevenZipResult | None = None

    for password in attempts:
        result = run_7z_extract_once(archive_path, output_dir, password=password)
        if result.success:
            return result

        last_result = result
        label = "无密码" if password is None else "已提供密码"
        detail = result.error or result.stderr.strip() or result.stdout.strip() or f"return code: {result.return_code}"
        errors.append(f"{label}: {detail}")

        if result.error == MISSING_7Z_MESSAGE:
            break

    if last_result is None:
        return SevenZipResult(
            success=False,
            used_password=None,
            return_code=-1,
            stdout="",
            stderr="",
            error="未执行 7z 解压尝试",
        )

    summary = f"7z 解压失败，共尝试 {len(errors)} 次。"
    if errors:
        summary = f"{summary}\n" + "\n".join(errors)

    return SevenZipResult(
        success=False,
        used_password=last_result.used_password,
        return_code=last_result.return_code,
        stdout=last_result.stdout,
        stderr=last_result.stderr,
        error=summary,
    )
