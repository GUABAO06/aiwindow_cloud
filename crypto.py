#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
crypto.py — 基于系统 openssl 命令行的 AES-256 加解密工具
=========================================================
- 零第三方依赖，跨架构通用（x86_64 / loongarch64 / aarch64 / riscv64 ...）
- 仅依赖系统 openssl 1.1+，银河麒麟 / 龙芯系统默认自带（sudo apt install openssl 可装）
- 提供文本加密 / 文本解密 / 文件加密 / 文件解密 四类接口
- 内部统一使用 AES-256-CBC + 随机salt + Base64 输出，兼容 openssl 命令行互操作
- ★ v1.6.8 跨平台修复：加密用 -A 输出单行 base64（无换行），解密对入参尾部换行
  归一化（strip 后补一个 \\n）。解决 Windows/新版 openssl(3.0) 默认 -a 输出 CRLF
  换行、种子存储后被 strip 掉尾部换行导致解密报 "error reading input file" 的问题；
  同时兼容历史 -a 生成的旧种子文件。"""

import os
import subprocess
import sys

_ALGO = "aes-256-cbc"


def _run(args, input_bytes=None):
    """执行 openssl 命令。失败时抛出带错误信息的 RuntimeError。"""
    kwargs = {
        "stdout": subprocess.PIPE,
        "stderr": subprocess.PIPE,
    }
    if input_bytes is not None:
        kwargs["input"] = input_bytes
    try:
        proc = subprocess.run(args, **kwargs)
    except FileNotFoundError:
        raise RuntimeError("未找到 openssl，请先安装：sudo apt install openssl")
    if proc.returncode != 0:
        err = (proc.stderr or b"").decode("utf-8", "replace").strip()
        raise RuntimeError("openssl 执行失败: %s" % err[-300:])
    return proc.stdout


def aes_encrypt_bytes(plain: bytes, password: str) -> bytes:
    """AES-256-CBC + salt 加密，输出单行 Base64 文本（utf-8 bytes，无换行）。
    使用 -A（单行 base64）避免不同平台 openssl 对 base64 换行处理不一致：
    Windows openssl 默认输出 CRLF 换行、Linux 输出 LF，若存储后被 strip 掉尾部
    换行，解密时 openssl 的 -a 行式 base64 读取器会报 "error reading input
    file"。-A 输出无换行，配合解密端归一化尾部换行，跨平台稳定互通。"""
    return _run(["openssl", "enc", "-" + _ALGO, "-a", "-A", "-salt", "-k", password],
                input_bytes=plain)


def aes_decrypt_bytes(data_b64: bytes, password: str) -> bytes:
    """解密 aes_encrypt_bytes 的输出，返回原始字节。
    对入参做尾部换行归一化：先剥离所有尾部空白/CR/LF，再补一个 \\n。既兼容本模块
    -A 生成的单行 base64（可能已被 strip 掉换行），也兼容历史上 openssl 默认 -a
    生成的多行/带 CRLF 的旧种子文件，避免 "error reading input file"。"""
    normalized = data_b64.rstrip(b" \t\r\n") + b"\n"
    return _run(["openssl", "enc", "-d", "-" + _ALGO, "-a", "-k", password],
                input_bytes=normalized)


def aes_encrypt_text(plain: str, password: str) -> str:
    """加密文本 -> 返回 base64 字符串。"""
    return aes_encrypt_bytes(plain.encode("utf-8"), password).decode("utf-8")


def aes_decrypt_text(data_b64: str, password: str) -> str:
    """解密 base64 字符串 -> 返回原始文本。"""
    return aes_decrypt_bytes(data_b64.encode("utf-8"), password).decode("utf-8")


def aes_encrypt_file(src_path: str, out_path: str, password: str, chmod: int = 0o600) -> None:
    """把任意文件加密为 .enc 文件（用于 build.sh 生成 seed_key.enc）。"""
    with open(src_path, "rb") as f:
        data = f.read()
    enc = aes_encrypt_bytes(data, password)
    with open(out_path, "wb") as f:
        f.write(enc)
    _chmod(out_path, chmod)


def aes_decrypt_file(enc_path: str, out_path: str, password: str) -> None:
    """解密 .enc 文件还原原始内容。"""
    with open(enc_path, "rb") as f:
        data = f.read()
    plain = aes_decrypt_bytes(data, password)
    with open(out_path, "wb") as f:
        f.write(plain)


def gen_password(length: int = 32) -> str:
    """用 openssl rand 生成随机 hex 密码串（用于 bootstrap.key）。"""
    out = _run(["openssl", "rand", "-hex", str(length)])
    return out.decode("utf-8").strip()


def _chmod(path: str, mode: int):
    try:
        os.chmod(path, mode)
    except OSError:
        pass


if __name__ == "__main__":
    # 自测：加解密往返
    _t = "划词助手 crypto self-test 12345"
    _p = gen_password(32)
    _e = aes_encrypt_text(_t, _p)
    _d = aes_decrypt_text(_e, _p)
    assert _t == _d, "round-trip failed"
    print("[crypto self-test] OK")
