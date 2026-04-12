"""
Drag-and-drop с tkinterdnd2 + CustomTkinter.
"""

from __future__ import annotations

import os
from typing import Any, Generator, Iterable

import tkinter as tk


def iter_ctk_drop_surfaces(widget: Any) -> Generator[Any, None, None]:
    """Вернуть tk-виджет(ы), на которых нужно регистрировать приём файлов."""
    canvas = getattr(widget, "_canvas", None)
    if canvas is not None:
        yield canvas
    else:
        yield widget


def parse_dropped_file_paths(root: tk.Misc, data: str) -> list[str]:
    """Разобрать строку из <<Drop>> (Tcl-список путей, часто в фигурных скобках)."""
    if not data or not str(data).strip():
        return []
    raw = str(data).strip()
    try:
        parts: Iterable[str] = root.tk.splitlist(raw)
    except tk.TclError:
        parts = [raw]
    out: list[str] = []
    for p in parts:
        s = p.strip().strip("{}").strip()
        if not s:
            continue
        out.append(os.path.normpath(s))
    return out
