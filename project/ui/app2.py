"""Главное окно: только главный поток трогает виджеты Tk."""

from __future__ import annotations

import queue
import threading
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Any, Optional

import customtkinter as ctk

import constants as C
from dnd_utils import iter_ctk_drop_surfaces, parse_dropped_file_paths
from logic import LOGIC_STOP, logic_worker_main
from logic.seismic import reorder_pipeline
from models import (
    LogicTaskValidateSeismic,
    PipeDragState,
    UiMessageValidateResult,
    UiMessageWorkerError,
)

try:
    from tkinterdnd2 import COPY, DND_FILES, TkinterDnD
except ImportError:
    COPY = None
    DND_FILES = None
    TkinterDnD = None


class App(ctk.CTk):
    def __init__(self) -> None:
        super().__init__()

        if TkinterDnD is not None:
            TkinterDnD._require(self)

        self.title("Seismic Data Suite")
        self.minsize(640, 480)
        self.configure(fg_color=C.WINDOW_BG)
        self._apply_fullscreen_geometry()

        self.current_state: dict[str, str] = {
            "tab": "Файл",
            "theme": "System",
            "scale": "100%",
        }
        self.history_tabs: list[str] = ["Файл"]
        self.history_index = 0
        self.is_navigating = False
        self._dnd_leave_timer: Optional[str] = None
        self.analysis_pipeline: list[str] = []
        self._pipe_drag: Optional[PipeDragState] = None

        self.current_file_path: Optional[str] = None
        self._load_request_id = 0
        self._applied_theme: Optional[str] = None
        self._applied_scale: Optional[str] = None
        self._file_loading: bool = False
        self._suspend_checkbox_cmd: bool = False

        self._shutdown = False
        self._ui_poll_id: Optional[str] = None
        self._logic_queue: queue.Queue = queue.Queue()
        self._ui_queue: queue.Queue = queue.Queue()
        self._logic_thread = threading.Thread(
            target=logic_worker_main,
            args=(self._logic_queue, self._ui_queue),
            name="AppLogic",
            daemon=True,
        )
        self._logic_thread.start()

        self.top_container = ctk.CTkFrame(self, fg_color=C.TOPBAR_BG, corner_radius=0)
        self.top_container.pack(fill="x", padx=12, pady=(8, 4))

        self.logo_wave = ctk.CTkLabel(
            self.top_container,
            text="≋",
            font=(C.FONT_LOGO[0], 26, "bold"),
            text_color=C.ACCENT,
            width=28,
        )
        self.logo_wave.pack(side="left", padx=(4, 2))
        self.logo_label = ctk.CTkLabel(
            self.top_container,
            text="SEIS",
            font=C.FONT_LOGO,
            text_color=C.ACCENT,
        )
        self.logo_label.pack(side="left", padx=(0, 12))

        self.nav_frame = ctk.CTkFrame(self.top_container, fg_color="transparent")
        self.nav_frame.pack(side="left")

        self.btn_back = ctk.CTkButton(
            self.nav_frame,
            text="←",
            width=34,
            height=32,
            corner_radius=6,
            fg_color=C.NAV_BTN_FG,
            hover_color=C.NAV_BTN_HOVER,
            text_color=C.NAV_BTN_TEXT,
            command=self.go_back,
        )
        self.btn_back.pack(side="left", padx=2)
        self.btn_forward = ctk.CTkButton(
            self.nav_frame,
            text="→",
            width=34,
            height=32,
            corner_radius=6,
            fg_color=C.NAV_BTN_FG,
            hover_color=C.NAV_BTN_HOVER,
            text_color=C.NAV_BTN_TEXT,
            command=self.go_forward,
        )
        self.btn_forward.pack(side="left", padx=2)

        self.tab_buttons: dict[str, ctk.CTkButton] = {}
        self.tabs_list = ["Файл", "Главная", "Данные", "Анализ", "Вид"]
        for name in self.tabs_list:
            btn = ctk.CTkButton(
                self.top_container,
                text=name,
                width=88,
                height=32,
                corner_radius=C.TAB_CORNER_RADIUS,
                border_width=0,
                fg_color=C.TAB_INACTIVE_FG,
                text_color=C.TAB_INACTIVE_TEXT,
                hover_color=C.TAB_HOVER,
                command=lambda n=name: self.save_state(n),
            )
            btn.pack(side="left", padx=3, pady=2)
            self.tab_buttons[name] = btn

        self.ribbon = ctk.CTkFrame(
            self,
            height=C.RIBBON_HEIGHT_DEFAULT,
            corner_radius=0,
            border_width=0,
            fg_color="transparent",
        )
        self.ribbon.pack(fill="x", padx=C.RIBBON_OUTER_PADX, pady=(2, 0))
        self.ribbon.pack_propagate(False)
        self._ribbon_stack = ctk.CTkFrame(self.ribbon, fg_color="transparent")
        self._ribbon_stack.pack(fill="both", expand=True)

        self._resize_after_id: Optional[str] = None
        self.bind("<Configure>", self._on_root_configure, add="+")

        self.container = ctk.CTkFrame(self, fg_color="transparent")
        self.container.pack(fill="both", expand=True, padx=C.RIBBON_OUTER_PADX, pady=(8, 10))
        self.container.grid_rowconfigure(0, weight=1)
        self.container.grid_columnconfigure(0, weight=1)

        self.frames: dict[str, ctk.CTkFrame] = {}
        for name in self.tabs_list:
            frame = ctk.CTkFrame(self.container, fg_color="transparent")
            self.frames[name] = frame
            frame.grid(row=0, column=0, sticky="nsew")

        self.setup_file_page()
        self.setup_ribbon_tools()
        self.setup_analysis_page()
        self.setup_view_settings()

        ctk.CTkLabel(
            self.frames["Главная"],
            text="Рабочая область: ГЛАВНАЯ",
            font=("Arial", 16, "italic"),
        ).pack(pady=50)
        ctk.CTkLabel(
            self.frames["Данные"],
            text="Когда-нибудь тут будут данные",
            font=("Arial", 24),
        ).pack(pady=100)

        self._setup_status_bar()
        self.apply_state(self.current_state)
        # Одна отложенная геометрия вместо прогрева всех вкладок / смены ленты при старте
        self.update_idletasks()
        self._bind_global_shortcuts()
        self._bind_upload_double_click()

        self.protocol("WM_DELETE_WINDOW", self._on_close_request)
        self._schedule_ui_drain()

    def _schedule_ui_drain(self) -> None:
        self._ui_poll_id = self.after(16, self._drain_ui_queue)

    def _drain_ui_queue(self) -> None:
        if self._shutdown:
            return
        try:
            while True:
                msg = self._ui_queue.get_nowait()
                self._handle_logic_message(msg)
        except queue.Empty:
            pass
        if not self._shutdown:
            self._schedule_ui_drain()

    def _handle_logic_message(self, msg: Any) -> None:
        if isinstance(msg, UiMessageValidateResult):
            if msg.request_id != self._load_request_id:
                return
            self._set_file_ui_busy(False)
            r = msg.result
            if r.ok and r.name:
                self.current_file_path = r.path
                self.file_status.configure(
                    text=f"Успешно загружен: {r.name}",
                    text_color=C.STATUS_OK,
                )
            elif r.error == "bad_ext":
                self.current_file_path = None
                self.file_status.configure(
                    text="Перетащите файл .sgy или .segy",
                    text_color=C.STATUS_WARN,
                )
            elif r.error == "not_file":
                self.current_file_path = None
                self.file_status.configure(
                    text="Файл не найден",
                    text_color=C.STATUS_WARN,
                )
        elif isinstance(msg, UiMessageWorkerError):
            if msg.request_id != self._load_request_id:
                return
            self._set_file_ui_busy(False)
            self.current_file_path = None
            self.file_status.configure(text=msg.message, text_color=C.STATUS_WARN)

    def _on_close_request(self) -> None:
        self._shutdown = True
        if self._ui_poll_id is not None:
            try:
                self.after_cancel(self._ui_poll_id)
            except tk.TclError:
                pass
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except tk.TclError:
                pass
        self._resize_after_id = None
        self._logic_queue.put(LOGIC_STOP)
        self._logic_thread.join(timeout=2.0)
        self.destroy()

    def submit_load_seismic(self, path: str) -> None:
        self._load_request_id += 1
        self._set_file_ui_busy(True)
        self._logic_queue.put(LogicTaskValidateSeismic(path=path, request_id=self._load_request_id))

    def _apply_fullscreen_geometry(self) -> None:
        try:
            self.state("zoomed")
        except tk.TclError:
            self.update_idletasks()
            sw = self.winfo_screenwidth()
            sh = self.winfo_screenheight()
            self.geometry(f"{sw}x{sh}+0+0")

    def _setup_status_bar(self) -> None:
        self.status_bar = ctk.CTkFrame(
            self,
            height=40,
            corner_radius=0,
            fg_color=C.STATUS_BAR_BG,
            border_width=1,
            border_color=C.STATUS_BAR_BORDER,
        )
        self.status_bar.pack(side="bottom", fill="x", padx=0, pady=0)
        self.status_bar.pack_propagate(False)
        self.status_hint_label = ctk.CTkLabel(
            self.status_bar,
            text="",
            anchor="w",
            font=C.FONT_BODY,
            text_color=C.STATUS_BAR_TEXT,
            justify="left",
        )
        self.status_hint_label.pack(side="left", padx=14, pady=8, fill="x", expand=True)
        self.status_keys_label = ctk.CTkLabel(
            self.status_bar,
            text=C.STATUS_KEYS_DEFAULT,
            anchor="e",
            font=C.FONT_SMALL,
            text_color=C.STATUS_BAR_TEXT,
        )
        self.status_keys_label.pack(side="right", padx=14, pady=8)
        self._bind_nav_status_hints()

    def _bind_nav_status_hints(self) -> None:
        def hover(widget, tip: str):
            def on_enter(_e):
                self.status_hint_label.configure(text=tip)

            def on_leave(_e):
                self._refresh_status_bar()

            widget.bind("<Enter>", on_enter)
            widget.bind("<Leave>", on_leave)

        hover(self.btn_back, "Назад по истории вкладок")
        hover(self.btn_forward, "Вперёд по истории вкладок")

    def _refresh_status_bar(self) -> None:
        tab = self.current_state["tab"]
        self.status_hint_label.configure(text=C.TAB_STATUS_HINTS.get(tab, ""))
        keys = C.STATUS_KEYS_ANALYSIS if tab == "Анализ" else C.STATUS_KEYS_DEFAULT
        self.status_keys_label.configure(text=keys)

    def _set_file_ui_busy(self, busy: bool) -> None:
        self._file_loading = busy
        try:
            self.btn_select.configure(state="disabled" if busy else "normal")
        except (tk.TclError, AttributeError):
            pass
        if busy:
            self.file_status.configure(text="Проверка файла…", text_color=C.STATUS_PENDING)

    def _bind_global_shortcuts(self) -> None:
        def go_tab(event, idx: int) -> str:
            if 0 <= idx < len(self.tabs_list):
                self.save_state(self.tabs_list[idx])
            return "break"

        for i, _name in enumerate(self.tabs_list):
            self.bind_all(f"<Control-Key-{i + 1}>", lambda e, ix=i: go_tab(e, ix))

        self.bind_all("<Control-o>", lambda e: self._shortcut_open_file())
        self.bind_all("<Control-O>", lambda e: self._shortcut_open_file())

    def _shortcut_open_file(self, event=None) -> Optional[str]:
        if self.current_state["tab"] != "Файл":
            self.save_state("Файл")
        self.open_file_dialog()
        return "break"

    def _bind_upload_double_click(self) -> None:
        def on_double(event) -> str:
            self.open_file_dialog()
            return "break"

        widgets = (
            self.frames["Файл"],
            self.upload_box,
            self.upload_glyph,
            self.upload_title,
            self.upload_formats,
            self.upload_dnd_hint,
            self.file_status,
        )
        for w in widgets:
            for surf in iter_ctk_drop_surfaces(w):
                surf.bind("<Double-Button-1>", on_double)

    def _on_root_configure(self, event: tk.Event) -> None:
        if event.widget is not self:
            return
        if self._shutdown:
            return
        if self._resize_after_id is not None:
            try:
                self.after_cancel(self._resize_after_id)
            except tk.TclError:
                pass
        self._resize_after_id = self.after(100, self._on_resize_idle)

    def _on_resize_idle(self) -> None:
        self._resize_after_id = None
        if self._shutdown:
            return
        try:
            self.update_idletasks()
        except tk.TclError:
            pass

    def setup_ribbon_tools(self) -> None:
        self._ribbon_placeholder = ctk.CTkFrame(self._ribbon_stack, fg_color="transparent")
        self._ribbon_placeholder.place(x=0, y=0, relwidth=1, relheight=1)

        self.home_tools = ctk.CTkFrame(self._ribbon_stack, fg_color="transparent")

        def add_tool(icon: str, text: str, cmd) -> None:
            btn = ctk.CTkButton(
                self.home_tools,
                text=f"{icon}\n{text}",
                fg_color=C.TOOL_FG,
                text_color=C.TOOL_TEXT,
                hover_color=C.TOOL_HOVER,
                border_width=1,
                border_color=C.TOOL_BORDER,
                corner_radius=8,
                width=100,
                height=80,
                font=C.FONT_RIBBON,
                command=cmd,
            )
            btn.pack(side="left", padx=4, pady=10)

        add_tool("⚙️", "Настройки", lambda: None)
        add_tool("⏳", "Фильтр", lambda: None)
        add_tool("📈", "Амплитуда", lambda: None)
        self.home_tools.place(x=0, y=0, relwidth=1, relheight=1)

        self.analysis_tools = ctk.CTkFrame(self._ribbon_stack, fg_color="transparent")
        title_row = ctk.CTkFrame(self.analysis_tools, fg_color="transparent")
        title_row.pack(fill="x", pady=(1, 0))
        ctk.CTkLabel(
            title_row,
            text="Методы обработки",
            font=C.FONT_RIBBON_SECTION,
            text_color=C.GRAY_LABEL,
            anchor="center",
        ).pack(fill="x", padx=16, pady=0)

        body = ctk.CTkFrame(self.analysis_tools, fg_color="transparent")
        # Без expand — иначе тело съедает всю высоту ленты и даёт пустую серую зону под методами
        body.pack(fill="x", expand=False, padx=12, pady=(0, 6))

        left_col = ctk.CTkFrame(body, fg_color="transparent")
        left_col.pack(side="left", fill="x", padx=(8, 8), anchor="nw")

        self.analysis_method_checkboxes: dict[str, ctk.CTkCheckBox] = {}
        for mid, _full, _short in C.ANALYSIS_METHODS:
            lbl = C.ANALYSIS_RIBBON_LABELS.get(mid, mid)
            cb = ctk.CTkCheckBox(
                left_col,
                text=lbl,
                font=C.FONT_SMALL,
                height=20,
                checkbox_width=14,
                checkbox_height=14,
                fg_color=C.ACCENT,
                hover_color=C.ACCENT_DARK,
                border_width=2,
                border_color=C.GRAY_BORDER_IDLE,
                text_color=C.GRAY_TEXT,
                command=lambda m=mid: self._on_ribbon_method_checkbox(m),
            )
            cb.pack(anchor="w", pady=0, ipady=0, fill="x")
            self.analysis_method_checkboxes[mid] = cb

        self.btn_processing = ctk.CTkButton(
            left_col,
            text="Обработка",
            width=140,
            height=28,
            corner_radius=8,
            font=C.FONT_RIBBON,
            fg_color=C.ACCENT,
            hover_color=C.ACCENT_DARK,
            text_color="white",
            command=self._on_processing_click,
        )
        self.btn_processing.pack(anchor="w", pady=(2, 0))

        self.analysis_tools.place(x=0, y=0, relwidth=1, relheight=1)
        self._ribbon_placeholder.tkraise()

    def setup_analysis_page(self) -> None:
        f = self.frames["Анализ"]
        self.analysis_body = ctk.CTkFrame(f, fg_color="transparent")
        self.analysis_body.pack(fill="both", expand=True)

        left_col = ctk.CTkFrame(self.analysis_body, fg_color="transparent", width=C.LEFT_COL_W)
        left_col.pack(side="left", fill="y", anchor="nw")
        left_col.pack_propagate(False)

        self.analysis_pipeline_outer = ctk.CTkFrame(
            left_col,
            fg_color=C.PIPELINE_CARD_FG,
            corner_radius=C.RIBBON_CORNER_RADIUS,
            border_width=1,
            border_color=C.PIPELINE_CARD_BORDER,
            width=C.PIPELINE_OUTER_W,
        )
        self.analysis_pipeline_outer.pack(side="top", anchor="nw", padx=0, pady=0)
        self.analysis_pipeline_outer.pack_propagate(False)

        ctk.CTkLabel(
            self.analysis_pipeline_outer,
            text="Цепочка обработки",
            font=C.FONT_HEAD,
            text_color=C.GRAY_TEXT,
            anchor="w",
        ).pack(anchor="w", fill="x", padx=14, pady=(14, 6))
        _sep1 = ctk.CTkFrame(
            self.analysis_pipeline_outer,
            height=2,
            corner_radius=0,
            fg_color=C.SEPARATOR_LINE,
        )
        _sep1.pack(fill="x", padx=12, pady=(0, 8))
        _sep1.pack_propagate(False)
        ctk.CTkLabel(
            self.analysis_pipeline_outer,
            text="Перетащите строки для порядка · клик по строке — убрать метод",
            font=C.FONT_SMALL,
            text_color=C.GRAY_TEXT_MUTED,
            justify="left",
            anchor="w",
        ).pack(anchor="w", padx=14, pady=(0, 6))

        self.analysis_pipeline_scroll = ctk.CTkScrollableFrame(
            self.analysis_pipeline_outer,
            fg_color="transparent",
            height=C.PIPELINE_SCROLL_HEIGHT,
        )
        self.analysis_pipeline_scroll.pack(fill="both", expand=True, padx=8, pady=(0, 12))

        self.analysis_workspace = ctk.CTkFrame(
            self.analysis_body,
            fg_color=C.ANALYSIS_WORKSPACE_BG,
            corner_radius=C.RIBBON_CORNER_RADIUS,
            border_width=1,
            border_color=C.ANALYSIS_WORKSPACE_BORDER,
        )
        self.analysis_workspace.pack(side="left", fill="both", expand=True, padx=(12, 0), pady=0)

        ws_head = ctk.CTkFrame(self.analysis_workspace, fg_color="transparent")
        ws_head.pack(fill="x", padx=14, pady=(14, 6))
        ctk.CTkLabel(
            ws_head,
            text="Рабочая область анализа",
            font=C.FONT_HEAD,
            text_color=C.GRAY_TEXT,
            anchor="w",
        ).pack(side="left", anchor="w")
        _sep2 = ctk.CTkFrame(
            self.analysis_workspace,
            height=2,
            corner_radius=0,
            fg_color=C.SEPARATOR_LINE,
        )
        _sep2.pack(fill="x", padx=12, pady=(0, 10))
        _sep2.pack_propagate(False)

        self.analysis_workspace_canvas = ctk.CTkFrame(
            self.analysis_workspace,
            fg_color=C.ANALYSIS_WORKSPACE_INNER,
            corner_radius=8,
            border_width=1,
            border_color=C.ANALYSIS_WORKSPACE_BORDER,
        )
        self.analysis_workspace_canvas.pack(fill="both", expand=True, padx=12, pady=(0, 12))

        self._refresh_analysis_ui()

    def _on_ribbon_method_checkbox(self, mid: str) -> None:
        if self._suspend_checkbox_cmd:
            return
        cb = self.analysis_method_checkboxes[mid]
        if cb.get():
            if mid not in self.analysis_pipeline:
                self.analysis_pipeline.append(mid)
        else:
            self.analysis_pipeline = [x for x in self.analysis_pipeline if x != mid]
        self._rebuild_pipeline_list()

    def _on_processing_click(self) -> None:
        if not self.analysis_pipeline:
            messagebox.showinfo(
                "Обработка",
                "Сначала отметьте один или несколько методов на ленте «Методы обработки».",
                parent=self,
            )
            return
        messagebox.showinfo(
            "Обработка",
            "Запуск цепочки обработки будет доступен в следующей версии.",
            parent=self,
        )

    def _analysis_label(self, mid: str) -> str:
        return C.ANALYSIS_LABELS.get(mid, mid)

    def toggle_analysis_method(self, mid: str) -> None:
        if mid in self.analysis_pipeline:
            self.analysis_pipeline.remove(mid)
        else:
            self.analysis_pipeline.append(mid)
        self._refresh_analysis_ui()

    def _sync_method_indicators(self) -> None:
        self._suspend_checkbox_cmd = True
        try:
            for mid, cb in self.analysis_method_checkboxes.items():
                if mid in self.analysis_pipeline:
                    cb.select()
                else:
                    cb.deselect()
        finally:
            self._suspend_checkbox_cmd = False

    def _refresh_analysis_ui(self) -> None:
        self._sync_method_indicators()
        self._rebuild_pipeline_list()

    def _pipeline_scroll_rows(self):
        return [
            c
            for c in self.analysis_pipeline_scroll.winfo_children()
            if isinstance(c, ctk.CTkFrame)
        ]

    def _pipeline_row_idle_style(self, row) -> None:
        if not row.winfo_exists():
            return
        row.configure(fg_color=C.GRAY_ROW, border_width=0, cursor="hand2")

    def _pipeline_row_slot_style(self, row, title_lbl) -> None:
        if row.winfo_exists():
            row.configure(
                fg_color=C.GRAY_ROW_ALT,
                border_width=1,
                border_color=("gray80", "#555"),
                cursor="none",
            )
        if title_lbl.winfo_exists():
            title_lbl.configure(text="")

    def _make_drag_ghost(self, mid: str, width_px: int):
        g = ctk.CTkToplevel(self)
        g.overrideredirect(True)
        try:
            g.attributes("-topmost", True)
        except tk.TclError:
            pass
        try:
            g.attributes("-alpha", 0.9)
        except tk.TclError:
            pass
        fr = ctk.CTkFrame(
            g,
            fg_color=("gray90", "#3a3a3a"),
            border_width=2,
            border_color=C.GHOST_BORDER,
            corner_radius=5,
            height=C.PIPELINE_ROW_HEIGHT,
        )
        fr.pack(fill="both", expand=True)
        ctk.CTkLabel(
            fr,
            text="☰",
            width=28,
            font=C.FONT_GRIP,
            text_color=("gray40", "gray65"),
        ).pack(side="left", padx=(4, 2))
        ctk.CTkLabel(
            fr,
            text=self._analysis_label(mid),
            font=C.FONT_SMALL,
            anchor="w",
        ).pack(side="left", fill="x", expand=True, padx=(4, 8))
        w = max(160, int(width_px))
        g.geometry(f"{w}x32+{-1000}+{-1000}")
        return g

    def _rebuild_pipeline_list(self) -> None:
        for w in self.analysis_pipeline_scroll.winfo_children():
            w.destroy()
        if not self.analysis_pipeline:
            ctk.CTkLabel(
                self.analysis_pipeline_scroll,
                text="Методы не выбраны",
                font=("Arial", 13),
                text_color="gray55",
            ).pack(pady=24)
            return
        _h = C.PIPELINE_ROW_HEIGHT
        for i, mid in enumerate(self.analysis_pipeline):
            row = ctk.CTkFrame(
                self.analysis_pipeline_scroll,
                fg_color=C.GRAY_ROW,
                height=_h,
                corner_radius=8,
                cursor="hand2",
            )
            row.pack(fill="x", pady=3, padx=2)
            row.pack_propagate(False)
            grip = ctk.CTkLabel(
                row,
                text="☰",
                width=28,
                font=C.FONT_GRIP,
                text_color=("gray40", "gray65"),
            )
            grip.pack(side="left", padx=(6, 2))
            title = ctk.CTkLabel(row, text=self._analysis_label(mid), font=C.FONT_SMALL, anchor="w")
            title.pack(side="left", fill="x", expand=True, padx=(4, 8))
            for w in (row, grip, title):
                w.bind(
                    "<Button-1>",
                    lambda e, idx=i, r=row, t=title, m=mid: self._pipeline_press(e, idx, r, t, m),
                )

    def _pipeline_press(self, event, idx: int, row, title_lbl, mid: str) -> None:
        gx = event.x_root - row.winfo_rootx()
        gy = event.y_root - row.winfo_rooty()
        self._pipe_drag = PipeDragState(
            idx=idx,
            mid=mid,
            x0=event.x_root,
            y0=event.y_root,
            moved=False,
            row=row,
            title_lbl=title_lbl,
            visual_on=False,
            hl_row=None,
            ghost=None,
            goffs=(gx, gy),
            ghost_w=180,
        )
        self.bind_all("<B1-Motion>", self._pipeline_motion_all)
        self.bind_all("<ButtonRelease-1>", self._pipeline_release_all)

    def _pipeline_motion_all(self, event) -> None:
        self._pipeline_motion_core(event.x_root, event.y_root)

    def _pipeline_motion_core(self, x_root: int, y_root: int) -> None:
        d = self._pipe_drag
        if not d:
            return
        if abs(x_root - d.x0) + abs(y_root - d.y0) > 6:
            if not d.visual_on:
                d.visual_on = True
                self.update_idletasks()
                row = d.row
                rw = max(150, row.winfo_width())
                d.ghost_w = rw
                d.ghost = self._make_drag_ghost(d.mid, rw)
                gx = x_root - d.goffs[0]
                gy = y_root - d.goffs[1]
                d.ghost.geometry(f"{rw}x32+{gx}+{gy}")
                self._pipeline_row_slot_style(row, d.title_lbl)
                try:
                    self.configure(cursor="fleur")
                except tk.TclError:
                    pass
            d.moved = True
            gh = d.ghost
            if gh is not None:
                try:
                    rw = d.ghost_w
                    ox, oy = d.goffs
                    gh.geometry(f"{rw}x32+{x_root - ox}+{y_root - oy}")
                except tk.TclError:
                    pass
            self._pipeline_update_drop_preview(y_root)

    def _pipeline_update_drop_preview(self, y_root: int) -> None:
        d = self._pipe_drag
        if not d or not d.visual_on:
            return
        drag_row = d.row
        rows = self._pipeline_scroll_rows()
        target_idx = self._pipeline_row_index_at_y(y_root)
        prev = d.hl_row
        if prev is not None and prev.winfo_exists() and prev is not drag_row:
            self._pipeline_row_idle_style(prev)
        d.hl_row = None
        if target_idx is None or not (0 <= target_idx < len(rows)):
            return
        target = rows[target_idx]
        if target is drag_row:
            return
        target.configure(
            fg_color=C.GRAY_ROW,
            border_width=2,
            border_color=C.DROP_PREVIEW_BORDER,
            cursor="hand2",
        )
        d.hl_row = target

    def _pipeline_release_all(self, event) -> None:
        d = self._pipe_drag
        if d is None:
            return
        self.unbind_all("<B1-Motion>")
        self.unbind_all("<ButtonRelease-1>")
        idx0 = d.idx
        mid = d.mid
        title_lbl = d.title_lbl
        drag_row = d.row
        moved = d.moved
        hl = d.hl_row
        ghost = d.ghost
        self._pipe_drag = None
        try:
            self.configure(cursor="")
        except tk.TclError:
            pass
        if ghost is not None:
            try:
                ghost.destroy()
            except tk.TclError:
                pass
        if moved:
            to = self._pipeline_row_index_at_y(event.y_root)
            if to is not None and to != idx0:
                reorder_pipeline(self.analysis_pipeline, idx0, to)
                self._refresh_analysis_ui()
            else:
                if drag_row and drag_row.winfo_exists():
                    self._pipeline_row_idle_style(drag_row)
                    if title_lbl.winfo_exists():
                        title_lbl.configure(text=self._analysis_label(mid))
                if hl and hl.winfo_exists() and hl is not drag_row:
                    self._pipeline_row_idle_style(hl)
        else:
            if 0 <= idx0 < len(self.analysis_pipeline):
                self.analysis_pipeline.pop(idx0)
                self._refresh_analysis_ui()

    def _pipeline_row_index_at_y(self, y_root: int):
        rows = self._pipeline_scroll_rows()
        for i, c in enumerate(rows):
            try:
                top = c.winfo_rooty()
                bot = top + c.winfo_height()
                if top <= y_root < bot:
                    return i
            except tk.TclError:
                continue
        return None

    def setup_file_page(self) -> None:
        self._upload_border_idle = C.UPLOAD_BORDER_IDLE
        self._upload_fg_idle = C.UPLOAD_FG_IDLE
        self._upload_dnd_hint_idle = (
            "Перетащите файл из проводника, дважды щёлкните по области\nили нажмите кнопку ниже (Ctrl+O)",
            ("gray40", "gray60"),
        )

        self.upload_box = ctk.CTkFrame(
            self.frames["Файл"],
            border_width=2,
            border_color=self._upload_border_idle,
            fg_color=self._upload_fg_idle,
            corner_radius=14,
            width=C.UPLOAD_BOX_W,
            height=C.UPLOAD_BOX_H,
        )
        self.upload_box.place(relx=0.5, rely=0.5, anchor="center")
        self.upload_box.pack_propagate(False)

        self.upload_glyph = ctk.CTkLabel(
            self.upload_box,
            text="⬇",
            font=C.FONT_ICON_LARGE,
            text_color=(C.ACCENT, C.ACCENT_LIGHT),
        )
        self.upload_glyph.pack(pady=(36, 0))

        self.upload_title = ctk.CTkLabel(self.upload_box, text="Область загрузки", font=C.FONT_TITLE)
        self.upload_title.pack(pady=(4, 6))
        self.upload_formats = ctk.CTkLabel(
            self.upload_box,
            text="Доступные форматы: .sgy, .segy",
            font=C.FONT_SUB,
            text_color=C.ACCENT,
        )
        self.upload_formats.pack(pady=(0, 8))

        self.upload_dnd_hint = ctk.CTkLabel(
            self.upload_box,
            text=self._upload_dnd_hint_idle[0],
            font=("Arial", 15),
            text_color=self._upload_dnd_hint_idle[1],
            justify="center",
        )
        self.upload_dnd_hint.pack(pady=(0, 14))

        self.btn_select = ctk.CTkButton(
            self.upload_box,
            text="Выбрать файл  (Ctrl+O)",
            width=240,
            height=50,
            font=("Arial", 16),
            command=self.open_file_dialog,
        )
        self.btn_select.pack(pady=10)

        self.file_status = ctk.CTkLabel(
            self.upload_box,
            text="Файл не выбран",
            font=C.FONT_SUB,
            text_color="gray",
        )
        self.file_status.pack(pady=(10, 36))

        self._register_file_drop_targets()

    def _register_file_drop_targets(self) -> None:
        if DND_FILES is None:
            return
        widgets = (
            self.frames["Файл"],
            self.upload_box,
            self.upload_glyph,
            self.upload_title,
            self.upload_formats,
            self.upload_dnd_hint,
            self.btn_select,
            self.file_status,
        )
        for w in widgets:
            for surf in iter_ctk_drop_surfaces(w):
                surf.drop_target_register(DND_FILES)
                surf.dnd_bind("<<Drop>>", self._on_file_drop)
                surf.dnd_bind("<<DropEnter>>", self._on_drop_enter)
                surf.dnd_bind("<<DropLeave>>", self._on_drop_leave)

    def _cancel_scheduled_drop_unhighlight(self) -> None:
        if self._dnd_leave_timer is not None:
            self.after_cancel(self._dnd_leave_timer)
            self._dnd_leave_timer = None

    def _on_drop_enter(self, event):
        if DND_FILES is None:
            return
        self._cancel_scheduled_drop_unhighlight()
        self._set_drop_zone_highlight(True)
        return COPY

    def _on_drop_leave(self, event) -> None:
        if DND_FILES is None:
            return

        def _unhighlight():
            self._dnd_leave_timer = None
            self._set_drop_zone_highlight(False)

        self._cancel_scheduled_drop_unhighlight()
        self._dnd_leave_timer = self.after(45, _unhighlight)

    def _set_drop_zone_highlight(self, active: bool) -> None:
        if active:
            self.upload_box.configure(
                border_width=3,
                border_color=C.UPLOAD_ACTIVE_BORDER,
                fg_color=C.UPLOAD_ACTIVE_FG,
            )
            self.upload_dnd_hint.configure(
                text="Отпустите файл здесь — он будет загружен",
                text_color=C.ACCENT,
            )
            self.upload_glyph.configure(text="📥", text_color=(C.ACCENT_DARK, C.ACCENT_HOVER))
        else:
            self.upload_box.configure(
                border_width=2,
                border_color=self._upload_border_idle,
                fg_color=self._upload_fg_idle,
            )
            self.upload_dnd_hint.configure(
                text=self._upload_dnd_hint_idle[0],
                text_color=self._upload_dnd_hint_idle[1],
            )
            self.upload_glyph.configure(
                text="⬇",
                text_color=(C.ACCENT, C.ACCENT_LIGHT),
            )

    def _on_file_drop(self, event) -> None:
        if DND_FILES is None:
            return
        self._cancel_scheduled_drop_unhighlight()
        self._set_drop_zone_highlight(False)
        for path in parse_dropped_file_paths(self, event.data):
            self.submit_load_seismic(path)
            break

    def open_file_dialog(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("Seismic data", "*.sgy *.segy")])
        if path:
            self.submit_load_seismic(path)

    def setup_view_settings(self) -> None:
        f = self.frames["Вид"]
        ctk.CTkLabel(f, text="Настройки интерфейса", font=("Arial", 24, "bold")).pack(pady=40)
        container = ctk.CTkFrame(f, fg_color="transparent")
        container.pack()
        for lbl, vals, opt in [
            ("Тема приложения:", ["System", "Dark", "Light"], "theme"),
            ("Масштаб:", ["80%", "100%", "120%"], "scale"),
        ]:
            r = ctk.CTkFrame(container, fg_color="transparent")
            r.pack(pady=10)
            ctk.CTkLabel(r, text=lbl, width=150, anchor="w", font=C.FONT_SUB).pack(side="left")
            menu = ctk.CTkOptionMenu(
                r,
                values=vals,
                command=lambda v, o=opt: self.update_view_settings(**{o: v}),
            )
            menu.pack(side="left")
            if opt == "theme":
                self.theme_menu = menu
            else:
                self.scale_menu = menu

    def save_state(self, tab: str) -> None:
        """История ведётся только по смене вкладок; тема и масштаб не добавляют шаги назад."""
        if self.is_navigating:
            return
        if self.current_state["tab"] == tab:
            return
        self.history_tabs = self.history_tabs[: self.history_index + 1]
        self.history_tabs.append(tab)
        self.history_index = len(self.history_tabs) - 1
        self.current_state["tab"] = tab
        self.apply_state(self.current_state)

    def update_view_settings(self, theme=None, scale=None) -> None:
        if self.is_navigating:
            return
        if theme:
            self.current_state["theme"] = theme
        if scale:
            self.current_state["scale"] = scale
        self.apply_state(self.current_state)

    def _apply_tab_ribbon(self, name: str) -> None:
        self.frames[name].tkraise()

        prev_tab = getattr(self, "_ribbon_style_tab", None)
        if prev_tab is None:
            for t_name, btn in self.tab_buttons.items():
                if t_name == name:
                    btn.configure(
                        fg_color=C.ACCENT,
                        text_color="white",
                        hover_color=C.ACCENT_DARK,
                    )
                else:
                    btn.configure(
                        fg_color=C.TAB_INACTIVE_FG,
                        text_color=C.TAB_INACTIVE_TEXT,
                        hover_color=C.TAB_HOVER,
                    )
        elif prev_tab != name:
            self.tab_buttons[prev_tab].configure(
                fg_color=C.TAB_INACTIVE_FG,
                text_color=C.TAB_INACTIVE_TEXT,
                hover_color=C.TAB_HOVER,
            )
            self.tab_buttons[name].configure(
                fg_color=C.ACCENT,
                text_color="white",
                hover_color=C.ACCENT_DARK,
            )
        self._ribbon_style_tab = name

        bucket = "home" if name == "Главная" else ("analysis" if name == "Анализ" else "none")
        if getattr(self, "_ribbon_bucket", None) == bucket:
            return
        self._ribbon_bucket = bucket

        if bucket == "home":
            self.home_tools.tkraise()
            self.ribbon.configure(
                height=C.RIBBON_HEIGHT_DEFAULT,
                fg_color=C.RIBBON_PANEL_BG,
                border_width=1,
                border_color=C.RIBBON_PANEL_BORDER,
                corner_radius=C.RIBBON_CORNER_RADIUS,
            )
        elif bucket == "analysis":
            self.analysis_tools.tkraise()
            self.ribbon.configure(
                height=C.RIBBON_HEIGHT_ANALYSIS,
                fg_color=C.RIBBON_PANEL_BG,
                border_width=1,
                border_color=C.RIBBON_PANEL_BORDER,
                corner_radius=C.RIBBON_CORNER_RADIUS,
            )
        else:
            self._ribbon_placeholder.tkraise()
            self.ribbon.configure(
                height=8,
                fg_color="transparent",
                border_width=0,
                corner_radius=0,
            )

    def _sync_theme_and_scale(self, state: dict[str, str]) -> None:
        ctk.set_appearance_mode(state["theme"])
        self.theme_menu.set(state["theme"])
        ctk.set_widget_scaling(int(state["scale"].replace("%", "")) / 100)
        self.scale_menu.set(state["scale"])

    def _sync_nav_buttons(self) -> None:
        can_back = self.history_index > 0
        can_fwd = self.history_index < len(self.history_tabs) - 1
        self.btn_back.configure(
            state="normal" if can_back else "disabled",
            fg_color=C.NAV_BTN_FG if can_back else C.NAV_BTN_DISABLED,
            text_color=C.NAV_BTN_TEXT if can_back else ("gray55", "gray50"),
            hover_color=C.NAV_BTN_HOVER if can_back else C.NAV_BTN_DISABLED,
        )
        self.btn_forward.configure(
            state="normal" if can_fwd else "disabled",
            fg_color=C.NAV_BTN_FG if can_fwd else C.NAV_BTN_DISABLED,
            text_color=C.NAV_BTN_TEXT if can_fwd else ("gray55", "gray50"),
            hover_color=C.NAV_BTN_HOVER if can_fwd else C.NAV_BTN_DISABLED,
        )

    def apply_state(self, state: dict[str, str]) -> None:
        self.is_navigating = True
        name = state["tab"]
        self._apply_tab_ribbon(name)
        # set_widget_scaling / set_appearance_mode трогают всё окно — только при смене темы или масштаба
        need_view = (
            self._applied_theme is None
            or self._applied_scale is None
            or state["theme"] != self._applied_theme
            or state["scale"] != self._applied_scale
        )
        if need_view:
            self._sync_theme_and_scale(state)
            self._applied_theme = state["theme"]
            self._applied_scale = state["scale"]
        self._sync_nav_buttons()
        self._refresh_status_bar()
        self.is_navigating = False

    def go_back(self) -> None:
        if self.history_index > 0:
            self.history_index -= 1
            self.current_state["tab"] = self.history_tabs[self.history_index]
            self.apply_state(self.current_state)

    def go_forward(self) -> None:
        if self.history_index < len(self.history_tabs) - 1:
            self.history_index += 1
            self.current_state["tab"] = self.history_tabs[self.history_index]
            self.apply_state(self.current_state)


def main() -> None:
    app = App()
    app.mainloop()
