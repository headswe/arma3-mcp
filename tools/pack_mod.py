#!/usr/bin/env python3
"""Assembles the ready-to-load @arma3_mcp mod folder.

Packs addons/main into an uncompressed PBO (Arma reads plain-text config.cpp
and .sqf fine) and copies the extension binaries to the mod root, where
callExtension looks for them:

    @arma3_mcp/addons/arma3_mcp_main.pbo
    @arma3_mcp/arma3_mcp_x64.dll
    @arma3_mcp/arma3_mcp_x64.so   (if given)
    @arma3_mcp/arma3_mcp.ini      (settings)

Usage: pack_mod.py <output dir> <extension binary>...
"""
import hashlib
import shutil
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def pack_pbo(src: Path, prefix: str, out: Path) -> None:
    files = sorted(p for p in src.rglob("*") if p.is_file() and p.name != "$PBOPREFIX$")
    header = bytearray()
    # Product entry carrying the PBO prefix.
    header += b"\0" + struct.pack("<5I", 0x56657273, 0, 0, 0, 0)
    header += b"prefix\0" + prefix.encode() + b"\0\0"
    data = bytearray()
    for f in files:
        content = f.read_bytes()
        name = str(f.relative_to(src)).replace("/", "\\")
        header += name.encode() + b"\0" + struct.pack("<5I", 0, len(content), 0, int(f.stat().st_mtime), len(content))
        data += content
    header += b"\0" + struct.pack("<5I", 0, 0, 0, 0, 0)
    body = bytes(header + data)
    out.write_bytes(body + b"\0" + hashlib.sha1(body).digest())


def main() -> None:
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    out_dir, binaries = Path(sys.argv[1]), [Path(p) for p in sys.argv[2:]]
    mod = out_dir / "@arma3_mcp"
    if mod.exists():
        shutil.rmtree(mod)
    (mod / "addons").mkdir(parents=True)

    src = ROOT / "addons" / "main"
    prefix = (src / "$PBOPREFIX$").read_text().strip()
    pack_pbo(src, prefix, mod / "addons" / "arma3_mcp_main.pbo")
    for binary in binaries:
        shutil.copy2(binary, mod / binary.name)
    shutil.copy2(ROOT / "arma3_mcp.ini", mod / "arma3_mcp.ini")
    shutil.copy2(ROOT / "README.md", mod / "README.md")
    print(f"wrote {mod}")


if __name__ == "__main__":
    main()
