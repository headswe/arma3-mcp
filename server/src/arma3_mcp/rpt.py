"""Finding and reading Arma 3 RPT log files on this machine."""

from __future__ import annotations

import os
import re
import sys
from collections import deque
from pathlib import Path

# Lines the engine writes for script errors, e.g.
#  Error in expression <...>
#  Error position: <...>
#  Error Undefined variable in expression: _foo
#  File \z\...\fn_x.sqf..., line 12
ERROR_LINE = re.compile(r"\bError\b|^\s*File .*, line \d+|Warning Message:")


def default_dirs() -> list[Path]:
    dirs = []
    env = os.environ.get("ARMA3_RPT_DIR")
    if env:
        dirs += [Path(p) for p in env.split(os.pathsep) if p]
    if sys.platform == "win32":
        local = os.environ.get("LOCALAPPDATA")
        if local:
            dirs.append(Path(local) / "Arma 3")
    else:
        home = Path.home()
        dirs += [home / ".local/share/bohemiainteractive/arma3", home / ".local/share/Arma 3"]
    return dirs


class RptReader:
    def __init__(self, dirs: list[Path] | None = None):
        self.dirs = dirs if dirs is not None else default_dirs()

    def files(self) -> list[Path]:
        found = []
        for d in self.dirs:
            if d.is_dir():
                found += [p for p in d.glob("*.rpt") if p.is_file()]
        return sorted(found, key=lambda p: p.stat().st_mtime, reverse=True)

    def latest(self) -> Path | None:
        files = self.files()
        return files[0] if files else None

    def resolve(self, name: str | None) -> Path:
        if not name:
            path = self.latest()
            if path is None:
                raise FileNotFoundError(
                    f"No .rpt files in {', '.join(map(str, self.dirs)) or '(no folders)'}; "
                    "set ARMA3_RPT_DIR or --rpt-dir to the folder with the logs (the -profiles folder for a server)."
                )
            return path
        for f in self.files():
            if f.name == name:
                return f
        raise FileNotFoundError(f"No RPT file named {name}")

    @staticmethod
    def tail(path: Path, lines: int, pattern: str | None = None, errors_only: bool = False) -> list[str]:
        regex = re.compile(pattern, re.IGNORECASE) if pattern else None
        out: deque[str] = deque(maxlen=max(1, lines))
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for line in fh:
                line = line.rstrip("\r\n")
                if errors_only and not ERROR_LINE.search(line):
                    continue
                if regex and not regex.search(line):
                    continue
                out.append(line)
        return list(out)

    def mark(self) -> tuple[Path, int] | None:
        """Where the newest log ends now, to read what gets written after."""
        path = self.latest()
        if path is None:
            return None
        try:
            return path, path.stat().st_size
        except OSError:
            return None

    @staticmethod
    def errors_since(mark: tuple[Path, int] | None, limit: int = 30) -> list[str]:
        if mark is None:
            return []
        path, offset = mark
        try:
            with path.open("rb") as fh:
                fh.seek(offset)
                text = fh.read(1024 * 1024).decode("utf-8", errors="replace")
        except OSError:
            return []
        return [ln for ln in text.splitlines() if ERROR_LINE.search(ln)][:limit]
