#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mkdeb.py — 纯 Python 的 deb 打包器（不依赖 dpkg-deb）
=========================================================
- 在无 dpkg-deb / 非 apt 系的系统（openEuler / 银河麒麟服务器版 / yum 系）兜底使用
- 仅用 Python 标准库（tarfile + 手写 ar 归档），跨架构通用
- 与 dpkg-deb --build 输出结构等价：ar 归档 = debian-binary + control.tar.gz + data.tar.gz
用法:
    python3 mkdeb.py <debroot目录> <输出.deb路径>
"""
import io
import os
import sys
import tarfile

MTIME = 0          # 固定 mtime，保证可复现构建
GZIP_LEVEL = 6


def _ar_member(name: str, data: bytes, mode: int = 0o644, mtime: int = MTIME) -> bytes:
    """构造一个 GNU ar 成员（60 字节定长头 + 数据，偶数字节补齐）。"""
    assert len(name) <= 16, "ar 成员名过长: %s" % name
    name_b = name.encode("ascii")
    mode_s = str(oct(mode)[2:])
    header = (
        name_b.ljust(16, b" ")
        + str(mtime).encode().ljust(12, b" ")
        + b"0".ljust(6, b" ")
        + b"0".ljust(6, b" ")
        + mode_s.encode().ljust(8, b" ")
        + str(len(data)).encode().ljust(10, b" ")
        + b"`\n"
    )
    out = bytearray(header)
    out += data
    if len(data) % 2:
        out += b"\n"
    return bytes(out)


def _make_control_tar(debroot: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT,
                      compresslevel=GZIP_LEVEL) as t:
        for name, mode in (("control", 0o644), ("postinst", 0o755),
                           ("postrm", 0o755), ("conffiles", 0o644), ("md5sums", 0o644)):
            p = os.path.join(debroot, "DEBIAN", name)
            if os.path.isfile(p):
                data = open(p, "rb").read()
                ti = tarfile.TarInfo("./" + name)
                ti.size = len(data)
                ti.mode = mode
                ti.uid = 0
                ti.gid = 0
                ti.mtime = MTIME
                t.addfile(ti, io.BytesIO(data))
    return buf.getvalue()


def _make_data_tar(debroot: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz", format=tarfile.GNU_FORMAT,
                      compresslevel=GZIP_LEVEL) as t:
        for cur, dirs, files in os.walk(debroot):
            dirs[:] = [d for d in dirs if d != "DEBIAN"]
            for d in dirs:
                dp = os.path.join(cur, d)
                ti = tarfile.TarInfo("./"
                                     + os.path.relpath(dp, debroot).replace("\\", "/") + "/")
                ti.type = tarfile.DIRTYPE
                ti.mode = 0o755
                ti.uid = 0
                ti.gid = 0
                ti.mtime = MTIME
                t.addfile(ti)
            for f in files:
                fp = os.path.join(cur, f)
                rel = os.path.relpath(fp, debroot).replace("\\", "/")
                name = "./" + rel
                st = os.stat(fp)
                ti = tarfile.TarInfo(name)
                ti.size = st.st_size
                ti.mode = st.st_mode & 0o777
                ti.uid = 0
                ti.gid = 0
                ti.mtime = MTIME
                with open(fp, "rb") as fh:
                    t.addfile(ti, fh)
    return buf.getvalue()


def make_deb(debroot: str, out_path: str) -> None:
    control_tar = _make_control_tar(debroot)
    data_tar = _make_data_tar(debroot)
    with open(out_path, "wb") as f:
        f.write(b"!<arch>\n")
        f.write(_ar_member("debian-binary", b"2.0\n"))
        f.write(_ar_member("control.tar.gz", control_tar))
        f.write(_ar_member("data.tar.gz", data_tar))


def main():
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(2)
    debroot, out = sys.argv[1], sys.argv[2]
    make_deb(debroot, out)
    print("mkdeb: %s -> %s (%d bytes)" % (debroot, out, os.path.getsize(out)))


if __name__ == "__main__":
    main()
