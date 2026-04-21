"""
Drag-and-drop с tkinterdnd2 + CustomTkinter.
"""

from __future__ import annotations

import os
import re
from typing import Any, Generator, Iterable
from urllib.parse import unquote

import tkinter as tk


def iter_ctk_drop_surfaces(widget: Any) -> Generator[Any, None, None]:
    """Вернуть tk-виджет(ы), на которых нужно регистрировать приём файлов."""
    canvas = getattr(widget, "_canvas", None)
    if canvas is not None:
        yield canvas
    else:
        yield widget


def _normalize_drop_path_segment(s: str) -> str:
    s = s.strip().strip("{}").strip().strip('"').strip("'")
    if not s:
        return ""
    if s.lower().startswith("file:"):
        s = unquote(s.replace("file:///", "").replace("file://", ""))
        if len(s) >= 2 and s[0] == "/" and s[2] == ":":
            s = s[1:]
    return os.path.normpath(s)


def parse_dropped_file_paths(root: tk.Misc, data: str) -> list[str]:
    """Разобрать строку из <<Drop>> (Tcl-список, фигурные скобки Windows, file://)."""
    if not data or not str(data).strip():
        return []
    raw = str(data).strip()
    out: list[str] = []
    seen: set[str] = set()

    def add(p: str) -> None:
        if p and p not in seen:
            seen.add(p)
            out.append(p)

    try:
        parts: Iterable[str] = root.tk.splitlist(raw)
    except tk.TclError:
        parts = []

    for p in parts:
        n = _normalize_drop_path_segment(p)
        if n:
            add(n)

    if not out:
        for m in re.finditer(r"\{([^}]*)\}", raw):
            n = _normalize_drop_path_segment(m.group(1))
            if n:
                add(n)

    if not out:
        n = _normalize_drop_path_segment(raw)
        if n:
            add(n)

    return out
