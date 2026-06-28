from __future__ import annotations

import argparse
import json
import re
import shutil
import struct
import sys
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from unpacker import PASSWORDS as MAIN_PASSWORDS

try:
    from Crypto.Cipher import AES
    from Crypto.Hash import HMAC, SHA1
    from Crypto.Protocol.KDF import PBKDF2
    from Crypto.Util import Counter
except Exception:
    AES = None
    HMAC = None
    PBKDF2 = None
    SHA1 = None
    Counter = None


ZIP_AES_METHOD = 99
AES_EXTRA_ID = 0x9901
AES_AUTH_CODE_SIZE = 10
CHUNK_SIZE = 1024 * 1024
ZIPFILE_SUPPORTED_METHODS = {
    zipfile.ZIP_STORED,
    zipfile.ZIP_DEFLATED,
    zipfile.ZIP_BZIP2,
    zipfile.ZIP_LZMA,
}


@dataclass
class AesMemberLayout:
    data_start: int
    salt_len: int
    key_len: int
    actual_compress_type: int
    encrypted_payload_size: int


def format_size(size: int) -> str:
    units = ("B", "KB", "MB", "GB", "TB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
        value /= 1024
    return f"{size} B"


def main_passwords() -> list[str]:
    result: list[str] = []
    for password in MAIN_PASSWORDS:
        if password and password not in result:
            result.append(password)
    return result


def parse_passwords(raw_passwords: list[str], raw_password_groups: list[str]) -> list[str]:
    passwords: list[str] = []
    for password in raw_passwords:
        if password and password not in passwords:
            passwords.append(password)
    for group in raw_password_groups:
        for password in group.split(","):
            password = password.strip()
            if password and password not in passwords:
                passwords.append(password)
    return passwords


def is_encrypted(info: zipfile.ZipInfo) -> bool:
    return bool(info.flag_bits & 0x1)


def safe_member_destination(output_dir: Path, member_name: str) -> Path | None:
    normalized = member_name.replace("\\", "/")
    if re.match(r"^[A-Za-z]:", normalized):
        return None

    rel = PurePosixPath(normalized)
    if rel.is_absolute() or any(part in ("", ".", "..") for part in rel.parts):
        return None

    dest = (output_dir / Path(*rel.parts)).resolve()
    root = output_dir.resolve()
    try:
        dest.relative_to(root)
    except ValueError:
        return None
    return dest


def member_to_record(info: zipfile.ZipInfo) -> dict[str, Any]:
    return {
        "filename": info.filename,
        "header_offset": info.header_offset,
        "file_size": info.file_size,
        "compress_size": info.compress_size,
        "compress_type": info.compress_type,
        "flag_bits": info.flag_bits,
        "encrypted": is_encrypted(info),
        "extract_version": info.extract_version,
        "create_version": info.create_version,
    }


def inspect_zip_container(archive_path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(archive_path, "r") as zf:
        infos = zf.infolist()
        if not infos:
            raise RuntimeError("ZIP 可以打开，但里面没有文件。")

        unsupported = sorted(
            {
                info.compress_type
                for info in infos
                if info.compress_type not in ZIPFILE_SUPPORTED_METHODS
            }
        )
        encrypted_count = sum(1 for info in infos if is_encrypted(info))
        min_header_offset = min(info.header_offset for info in infos)
        total_uncompressed = sum(info.file_size for info in infos)
        total_compressed = sum(info.compress_size for info in infos)

        return {
            "source": str(archive_path),
            "source_size": archive_path.stat().st_size,
            "zip_start_dir": getattr(zf, "start_dir", None),
            "zip_comment_size": len(zf.comment or b""),
            "min_header_offset": min_header_offset,
            "member_count": len(infos),
            "encrypted_count": encrypted_count,
            "unsupported_methods": unsupported,
            "has_aes_method_99": ZIP_AES_METHOD in unsupported,
            "total_uncompressed": total_uncompressed,
            "total_compressed": total_compressed,
            "members": [member_to_record(info) for info in infos],
        }


def write_json_cache(cache_dir: Path, archive_path: Path, payload: dict[str, Any]) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = cache_dir / f"{archive_path.stem}.zip-inspect.json"
    with manifest_path.open("w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2)
        f.write("\n")
    return manifest_path


def copy_full_zip_cache(source: Path, dest: Path, start: int, end: int) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    remaining = end - start
    if remaining <= 0:
        raise RuntimeError("ZIP 范围为空。")

    with source.open("rb") as src, dest.open("wb") as out:
        src.seek(start)
        while remaining:
            chunk = src.read(min(8 * CHUNK_SIZE, remaining))
            if not chunk:
                raise RuntimeError("复制 ZIP 数据时提前到达文件末尾。")
            out.write(chunk)
            remaining -= len(chunk)


def read_aes_member_layout(archive_path: Path, info: zipfile.ZipInfo) -> AesMemberLayout:
    with archive_path.open("rb") as f:
        f.seek(info.header_offset)
        header = f.read(30)
        if len(header) != 30:
            raise RuntimeError(f"读取本地文件头失败：{info.filename}")

        (
            signature,
            _version_needed,
            _flags,
            compress_type,
            _mod_time,
            _mod_date,
            _crc32,
            _compress_size_32,
            _file_size_32,
            filename_len,
            extra_len,
        ) = struct.unpack("<4sHHHHHIIIHH", header)

        if signature != b"PK\x03\x04":
            raise RuntimeError(f"本地文件头签名不正确：{info.filename}")
        if compress_type != ZIP_AES_METHOD:
            raise RuntimeError(f"不是 WinZip AES method 99 成员：{info.filename}")

        f.seek(filename_len, 1)
        extra = f.read(extra_len)
        data_start = f.tell()

    pos = 0
    aes_body: bytes | None = None
    while pos + 4 <= len(extra):
        header_id, size = struct.unpack("<HH", extra[pos : pos + 4])
        body = extra[pos + 4 : pos + 4 + size]
        if header_id == AES_EXTRA_ID:
            aes_body = body
            break
        pos += 4 + size

    if aes_body is None or len(aes_body) < 7:
        raise RuntimeError(f"没有找到 WinZip AES 扩展字段：{info.filename}")

    _version, _vendor, strength, actual_compress_type = struct.unpack("<H2sBH", aes_body[:7])
    salt_len_by_strength = {1: 8, 2: 12, 3: 16}
    key_len_by_strength = {1: 16, 2: 24, 3: 32}
    if strength not in salt_len_by_strength:
        raise RuntimeError(f"不支持的 AES 强度：{strength}")

    encrypted_payload_size = info.compress_size - salt_len_by_strength[strength] - 2 - AES_AUTH_CODE_SIZE
    if encrypted_payload_size < 0:
        raise RuntimeError(f"AES 成员大小不合法：{info.filename}")

    return AesMemberLayout(
        data_start=data_start,
        salt_len=salt_len_by_strength[strength],
        key_len=key_len_by_strength[strength],
        actual_compress_type=actual_compress_type,
        encrypted_payload_size=encrypted_payload_size,
    )


def make_decompressor(compress_type: int):
    if compress_type == zipfile.ZIP_STORED:
        return None
    if compress_type == zipfile.ZIP_DEFLATED:
        return zlib.decompressobj(-15)
    raise RuntimeError(f"暂不支持 AES 内层压缩方法：{compress_type}")


def extract_aes_member_once(
    archive_path: Path,
    info: zipfile.ZipInfo,
    output_dir: Path,
    password: str,
) -> bool:
    if AES is None or PBKDF2 is None or SHA1 is None or HMAC is None or Counter is None:
        raise RuntimeError("当前 Python 环境缺少 Crypto 库，不能直接解 WinZip AES。")

    dest = safe_member_destination(output_dir, info.filename)
    if dest is None:
        raise RuntimeError(f"ZIP 内存在不安全路径，已拒绝解压：{info.filename}")

    layout = read_aes_member_layout(archive_path, info)
    if info.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        return True

    dest.parent.mkdir(parents=True, exist_ok=True)
    temp_dest = dest.with_name(dest.name + ".partial")
    if temp_dest.exists():
        temp_dest.unlink()

    with archive_path.open("rb") as src:
        src.seek(layout.data_start)
        salt = src.read(layout.salt_len)
        stored_password_verify = src.read(2)
        key_material = PBKDF2(
            password.encode("utf-8"),
            salt,
            dkLen=2 * layout.key_len + 2,
            count=1000,
            hmac_hash_module=SHA1,
        )
        encryption_key = key_material[: layout.key_len]
        hmac_key = key_material[layout.key_len : 2 * layout.key_len]
        password_verify = key_material[2 * layout.key_len :]
        if password_verify != stored_password_verify:
            return False

        counter = Counter.new(128, initial_value=1, little_endian=True)
        cipher = AES.new(encryption_key, AES.MODE_CTR, counter=counter)
        hmac = HMAC.new(hmac_key, digestmod=SHA1)
        decompressor = make_decompressor(layout.actual_compress_type)
        remaining = layout.encrypted_payload_size

        try:
            with temp_dest.open("wb") as out:
                while remaining:
                    chunk = src.read(min(CHUNK_SIZE, remaining))
                    if not chunk:
                        raise RuntimeError("读取 AES 加密数据时提前到达文件末尾。")
                    remaining -= len(chunk)
                    hmac.update(chunk)
                    decrypted = cipher.decrypt(chunk)
                    if decompressor is None:
                        out.write(decrypted)
                    else:
                        out.write(decompressor.decompress(decrypted))

                if decompressor is not None:
                    out.write(decompressor.flush())

                stored_auth_code = src.read(AES_AUTH_CODE_SIZE)
                if len(stored_auth_code) != AES_AUTH_CODE_SIZE:
                    raise RuntimeError("读取 AES 校验码失败。")
                if hmac.digest()[:AES_AUTH_CODE_SIZE] != stored_auth_code:
                    raise RuntimeError("AES 校验失败，可能是密码错误或文件损坏。")

            if dest.exists():
                dest.unlink()
            temp_dest.rename(dest)
            return True
        except Exception:
            if temp_dest.exists():
                temp_dest.unlink()
            raise


def extract_aes_member(
    archive_path: Path,
    info: zipfile.ZipInfo,
    output_dir: Path,
    passwords: list[str],
) -> None:
    if not passwords:
        raise RuntimeError(f"AES ZIP 成员需要密码：{info.filename}")

    for password in passwords:
        if extract_aes_member_once(archive_path, info, output_dir, password):
            print(f"[AES] 解密成功：{info.filename}")
            return

    raise RuntimeError(f"AES ZIP 密码均不匹配：{info.filename}")


def extract_member_with_zipfile(
    zf: zipfile.ZipFile,
    info: zipfile.ZipInfo,
    output_dir: Path,
    passwords: list[str],
) -> None:
    dest = safe_member_destination(output_dir, info.filename)
    if dest is None:
        raise RuntimeError(f"ZIP 内存在不安全路径，已拒绝解压：{info.filename}")

    if info.is_dir():
        dest.mkdir(parents=True, exist_ok=True)
        return

    dest.parent.mkdir(parents=True, exist_ok=True)
    password_candidates: list[bytes | None] = [None]
    if is_encrypted(info):
        password_candidates = [password.encode("utf-8") for password in passwords]
        if not password_candidates:
            raise RuntimeError(f"ZIP 成员需要密码，但没有提供密码：{info.filename}")

    last_error: Exception | None = None
    for password in password_candidates:
        try:
            with zf.open(info, "r", pwd=password) as src, dest.open("wb") as out:
                shutil.copyfileobj(src, out, length=CHUNK_SIZE)
            return
        except Exception as exc:
            last_error = exc
            if dest.exists():
                dest.unlink()

    raise RuntimeError(f"解压 ZIP 成员失败：{info.filename}；最后错误：{last_error}")


def extract_members(archive_path: Path, output_dir: Path, passwords: list[str]) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    extracted = 0
    with zipfile.ZipFile(archive_path, "r") as zf:
        for info in zf.infolist():
            if info.compress_type == ZIP_AES_METHOD:
                extract_aes_member(archive_path, info, output_dir, passwords)
            else:
                extract_member_with_zipfile(zf, info, output_dir, passwords)
            if not info.is_dir():
                extracted += 1
    return extracted


def default_cache_dir() -> Path:
    return Path(__file__).resolve().parent / "cache" / "mp4_embedded_zip_zipfile"


def default_output_dir(archive_path: Path) -> Path:
    return Path(__file__).resolve().parent / "_mp4_zipfile_output" / archive_path.stem


def print_inspection(inspection: dict[str, Any]) -> None:
    print(f"[校验] zipfile 可直接打开源文件，成员数：{inspection['member_count']}")
    print(f"[定位] 真实 ZIP 首个本地头偏移：{inspection['min_header_offset']}")
    print(f"[定位] 中央目录偏移：{inspection['zip_start_dir']}")
    print(f"[大小] 源文件：{format_size(inspection['source_size'])}")
    print(f"[大小] ZIP 成员压缩后合计：{format_size(inspection['total_compressed'])}")
    print(f"[大小] ZIP 成员解压后合计：{format_size(inspection['total_uncompressed'])}")
    if inspection["encrypted_count"]:
        print(f"[加密] 加密成员数：{inspection['encrypted_count']}")
    if inspection["unsupported_methods"]:
        methods = ", ".join(str(method) for method in inspection["unsupported_methods"])
        print(f"[兼容] Python zipfile 不支持的压缩方法：{methods}")
    for member in inspection["members"][:10]:
        print(
            "[成员] "
            f"{member['filename']} | method={member['compress_type']} | "
            f"size={format_size(member['file_size'])} | "
            f"compressed={format_size(member['compress_size'])} | "
            f"header_offset={member['header_offset']}"
        )
    if len(inspection["members"]) > 10:
        print(f"[成员] 其余 {len(inspection['members']) - 10} 个成员已省略")


def main() -> int:
    parser = argparse.ArgumentParser(
        description=(
            "实验：MP4 伪装 ZIP 直接读源文件；method 99 用 Python AES 流式解密，"
            "默认不复制完整 ZIP 到 SSD。"
        )
    )
    parser.add_argument("archive", help="要测试的 MP4 伪装压缩文件")
    parser.add_argument("output_dir", nargs="?", help="可选：解压输出目录")
    parser.add_argument("--cache-dir", default=None, help="保存轻量诊断缓存的目录")
    parser.add_argument("--password", action="append", default=[], help="可重复追加单个 ZIP 密码")
    parser.add_argument("--passwords", action="append", default=[], help="逗号分隔的追加 ZIP 密码池")
    parser.add_argument(
        "--no-main-passwords",
        action="store_true",
        help="不读取主脚本 unpacker.py 里的 PASSWORDS",
    )
    parser.add_argument(
        "--inspect-only",
        action="store_true",
        help="只分析 ZIP 目录并写入缓存，不解压大文件",
    )
    parser.add_argument(
        "--write-full-cache-zip",
        action="store_true",
        help="危险：把 ZIP 有效区完整复制到 cache，会大量写入 SSD，仅保留做对照实验",
    )
    args = parser.parse_args()

    archive_path = Path(args.archive).resolve()
    if not archive_path.exists():
        print(f"[结果] 失败：文件不存在：{archive_path}")
        return 1
    if not archive_path.is_file():
        print(f"[结果] 失败：不是文件：{archive_path}")
        return 1

    output_dir = Path(args.output_dir).resolve() if args.output_dir else default_output_dir(archive_path)
    cache_dir = Path(args.cache_dir).resolve() if args.cache_dir else default_cache_dir()
    raw_passwords = [] if args.no_main_passwords else main_passwords()
    raw_passwords.extend(args.password)
    passwords = parse_passwords(raw_passwords, args.passwords)

    print(f"[输入] {archive_path}")
    print(f"[输出] {output_dir}")
    print(f"[缓存] {cache_dir}")
    print(f"[密码] 已从主脚本读取 {len(main_passwords()) if not args.no_main_passwords else 0} 个密码")
    print("[SSD] 默认只写轻量 JSON 缓存，不复制完整 ZIP 数据")

    try:
        inspection = inspect_zip_container(archive_path)
        print_inspection(inspection)

        manifest_path = write_json_cache(cache_dir, archive_path, inspection)
        print(f"[缓存] 已保留诊断数据：{manifest_path}")

        if args.write_full_cache_zip:
            start = int(inspection["min_header_offset"])
            end = int(inspection["source_size"])
            full_zip_path = cache_dir / f"{archive_path.stem}.extracted.full-copy.zip"
            print("[SSD] 即将写入完整 ZIP 副本，这是高写入量操作")
            copy_full_zip_cache(archive_path, full_zip_path, start, end)
            print(f"[缓存] 已写入完整 ZIP 副本：{full_zip_path}")

        if args.inspect_only:
            print("[结果] 完成：只分析，不解压")
            return 0

        if inspection["has_aes_method_99"]:
            print("[方案] 检测到 AES method 99，改用 Python AES 流式解密，不调用 7z 硬链接")

        extracted = extract_members(archive_path, output_dir, passwords)
        print(f"[结果] 成功：已解压文件数 {extracted}")
        return 0
    except Exception as exc:
        print(f"[结果] 失败：{exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
