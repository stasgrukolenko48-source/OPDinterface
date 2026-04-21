"""Главное окно: только главный поток трогает виджеты Tk."""

from __future__ import annotations

import queue
import locale
import threading
import gc
import tkinter as tk
from tkinter import filedialog, messagebox
from typing import Any, Callable, Optional

import customtkinter as ctk

import constants as C
from dnd_utils import iter_ctk_drop_surfaces, parse_dropped_file_paths
from logic import LOGIC_STOP, logic_worker_main
from logic.seismic import reorder_pipeline
from models import (
    LogicTaskValidateSeismic,
    PipeDragState,
    SeismicPreview,
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

        # Вкладка «Данные» / testtrass: метаданные и матрица трасс
        self.total_traces: int = 0
        self.samples_count: int = 0
        self.matrix_data: Any = None
        self._home_selection_patch: Any = None
        self._home_drag_anchor: Optional[int] = None
        self._home_view_start: int = 0
        self._home_view_end: int = 0
        self._home_view_step: int = 1

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
        self.setup_home_page()
        self.setup_data_page()

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
                self.total_traces = int(r.tracecount or 0)
                self.samples_count = int(r.samples_count or 0)
                self.matrix_data = None
                self._sync_data_tab_after_load()
                self._update_home_plots_after_load(r.preview)
            elif r.error == "bad_ext":
                self.current_file_path = None
                self.file_status.configure(
                    text="Перетащите файл .sgy или .segy",
                    text_color=C.STATUS_WARN,
                )
                self._reset_data_tab_state()
                self._reset_home_plots_empty()
            elif r.error == "not_file":
                self.current_file_path = None
                self.file_status.configure(
                    text="Файл не найден",
                    text_color=C.STATUS_WARN,
                )
                self._reset_data_tab_state()
                self._reset_home_plots_empty()
            elif r.error == "not_readable":
                self.current_file_path = None
                self.file_status.configure(
                    text="Файл не читается как SEG-Y",
                    text_color=C.STATUS_WARN,
                )
                self._reset_data_tab_state()
                self._reset_home_plots_empty()
        elif isinstance(msg, UiMessageWorkerError):
            if msg.request_id != self._load_request_id:
                return
            self._set_file_ui_busy(False)
            self.current_file_path = None
            self.file_status.configure(text=msg.message, text_color=C.STATUS_WARN)
            self._reset_data_tab_state()
            self._reset_home_plots_empty()

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
        path = str(path).strip().strip("{}").strip().strip('"').strip("'")
        if path.lower().startswith("file:///"):
            path = path[8:]
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
        for mid, full, _ in C.ANALYSIS_METHODS:
            lbl = full
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
        self.analysis_status_label = ctk.CTkLabel(
            self.analysis_workspace_canvas,
            text="Выберите порядок методов и нажмите «Обработка».",
            font=C.FONT_BODY,
            text_color=C.GRAY_TEXT,
            justify="left",
            anchor="w",
        )
        self.analysis_status_label.pack(fill="x", padx=12, pady=(12, 8))
        self.analysis_progress = ctk.CTkProgressBar(self.analysis_workspace_canvas)
        self.analysis_progress.pack(fill="x", padx=12, pady=(0, 12))
        self.analysis_progress.set(0.0)

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
        if not self.current_file_path:
            messagebox.showinfo(
                "Обработка",
                "Сначала загрузите SEG-Y файл на вкладке «Файл».",
                parent=self,
            )
            return

        method_map: dict[str, Callable[[Any], Any]] = {
            "interp": self._method_interp,
            "denoise": self._method_denoise,
            "spectrum": self._method_spectrum,
            "resolution": self._method_resolution,
        }
        ordered_methods = [m for m in self.analysis_pipeline if m in method_map]
        if not ordered_methods:
            messagebox.showwarning(
                "Обработка",
                "В цепочке нет поддерживаемых методов обработки.",
                parent=self,
            )
            return

        try:
            import numpy as np
            import segyio

            start = int(self.entry_data_start.get()) if self.entry_data_start.get().strip() else 0
            end = int(self.entry_data_end.get()) if self.entry_data_end.get().strip() else self.total_traces
            step = int(self.entry_data_step.get()) if self.entry_data_step.get().strip() else 1
            if step <= 0:
                raise ValueError("Шаг должен быть больше 0.")
            if start < 0 or end > self.total_traces or start >= end:
                raise ValueError(f"Диапазон должен быть в пределах 0..{self.total_traces}, и От < До.")
            selected_traces = len(range(start, end, step))
            if selected_traces <= 0:
                raise ValueError("Выбран пустой диапазон данных.")

            chunk_size = 1000
            total_max = 0.0
            processed = 0
            self.analysis_progress.set(0.0)
            self.analysis_status_label.configure(
                text=(
                    f"Запуск: {', '.join(self._analysis_label(m) for m in ordered_methods)} "
                    f"(трассы {start}:{end}:{step})"
                ),
                text_color=C.STATUS_PENDING,
            )
            self.update_idletasks()

            with segyio.open(self.current_file_path, "r", ignore_geometry=True, strict=False) as f:
                total_traces_in_file = int(f.tracecount)
                if total_traces_in_file <= 0:
                    raise ValueError("В файле нет трасс для обработки.")

                for offset in range(0, selected_traces, chunk_size):
                    part_end = min(offset + chunk_size, selected_traces)
                    from_trace = start + offset * step
                    to_trace = start + part_end * step
                    current_chunk = np.asarray(f.trace.raw[from_trace:to_trace:step], dtype=np.float32)

                    for method_id in ordered_methods:
                        current_chunk = method_map[method_id](current_chunk)

                    if current_chunk.size > 0:
                        total_max = max(total_max, float(np.max(np.abs(current_chunk))))

                    processed += (part_end - offset)
                    self.analysis_progress.set(processed / selected_traces)
                    self.analysis_status_label.configure(
                        text=(
                            f"Обработаны выбранные трассы {from_trace}-{to_trace} из диапазона {start}:{end}:{step} "
                            f"({int(100 * processed / selected_traces)}%)."
                        ),
                        text_color=C.GRAY_TEXT,
                    )
                    self.update_idletasks()

                    del current_chunk
                    if processed % (chunk_size * 5) == 0:
                        gc.collect()

            self.analysis_status_label.configure(
                text=(
                    "Готово. Порядок методов: "
                    f"{' -> '.join(self._analysis_label(m) for m in ordered_methods)}. "
                    f"max|A|={total_max:.3g}"
                ),
                text_color=C.STATUS_OK,
            )
            self.analysis_progress.set(1.0)
        except Exception as ex:
            self.analysis_status_label.configure(text=f"Ошибка конвейера: {ex}", text_color=C.STATUS_WARN)
            self.analysis_progress.set(0.0)

    def _method_interp(self, chunk: Any) -> Any:
        # Заглушка для будущей интерполяции: оставляем формат массива и тип.
        return chunk * 1.0

    def _method_denoise(self, chunk: Any) -> Any:
        import numpy as np

        arr = np.asarray(chunk, dtype=np.float32)
        if arr.ndim != 2 or arr.shape[1] < 3:
            return arr
        out = arr.copy()
        out[:, 1:-1] = (arr[:, :-2] + arr[:, 1:-1] + arr[:, 2:]) / 3.0
        return out

    def _method_spectrum(self, chunk: Any) -> Any:
        import numpy as np

        arr = np.asarray(chunk, dtype=np.float32)
        return np.clip(arr * 1.1, -1.0e9, 1.0e9)

    def _method_resolution(self, chunk: Any) -> Any:
        import numpy as np

        arr = np.asarray(chunk, dtype=np.float32)
        if arr.ndim != 2 or arr.shape[1] < 3:
            return arr
        out = arr.copy()
        mid = arr[:, 1:-1]
        out[:, 1:-1] = mid + 0.25 * (mid - (arr[:, :-2] + arr[:, 2:]) * 0.5)
        return out

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
        raw = getattr(event, "data", "") or ""
        if isinstance(raw, bytes):
            try:
                raw = raw.decode("utf-8")
            except UnicodeDecodeError:
                raw = raw.decode(locale.getpreferredencoding(False), errors="replace")
        else:
            raw = str(raw)
        for path in parse_dropped_file_paths(self, raw):
            self.submit_load_seismic(path)
            break
        return COPY

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

    def setup_data_page(self) -> None:
        """Диапазон трасс и чтение в память (логика как в testtrass.py)."""
        f = self.frames["Данные"]
        root = ctk.CTkFrame(f, fg_color="transparent")
        root.pack(fill="both", expand=True, padx=16, pady=16)

        ctk.CTkLabel(
            root,
            text="Выбор данных",
            font=C.FONT_HEAD,
            text_color=C.GRAY_TEXT,
            anchor="w",
        ).pack(anchor="w", pady=(0, 8))

        self.label_data_meta = ctk.CTkLabel(
            root,
            text="Файл не загружен — сначала откройте SEG-Y на вкладке «Файл».",
            font=C.FONT_BODY,
            text_color=C.GRAY_TEXT_MUTED,
            justify="left",
            anchor="w",
        )
        self.label_data_meta.pack(anchor="w", pady=(0, 14))

        row = ctk.CTkFrame(root, fg_color="transparent")
        row.pack(anchor="w", pady=(0, 12))

        ctk.CTkLabel(row, text="От:", font=C.FONT_BODY, text_color=C.GRAY_TEXT).grid(row=0, column=0, padx=(0, 6))
        self.entry_data_start = ctk.CTkEntry(row, placeholder_text="0", width=88, state="disabled")
        self.entry_data_start.grid(row=0, column=1, padx=(0, 16))

        ctk.CTkLabel(row, text="До:", font=C.FONT_BODY, text_color=C.GRAY_TEXT).grid(row=0, column=2, padx=(0, 6))
        self.entry_data_end = ctk.CTkEntry(row, placeholder_text="—", width=88, state="disabled")
        self.entry_data_end.grid(row=0, column=3, padx=(0, 16))

        ctk.CTkLabel(row, text="Шаг:", font=C.FONT_BODY, text_color=C.GRAY_TEXT).grid(row=0, column=4, padx=(0, 6))
        self.entry_data_step = ctk.CTkEntry(row, placeholder_text="1", width=64, state="disabled")
        self.entry_data_step.grid(row=0, column=5, padx=(0, 0))
        self.entry_data_start.bind("<FocusOut>", self._on_data_entries_focus_out)
        self.entry_data_end.bind("<FocusOut>", self._on_data_entries_focus_out)
        self.entry_data_step.bind("<FocusOut>", self._on_data_entries_focus_out)

        ctk.CTkLabel(
            root,
            text="Выбор мышкой выполняется на вкладке «Главная», график «До».",
            font=C.FONT_SMALL,
            text_color=C.GRAY_TEXT_MUTED,
            anchor="w",
            justify="left",
        ).pack(anchor="w", pady=(2, 8))

        self.btn_data_read = ctk.CTkButton(
            root,
            text="Выбрать данные",
            width=200,
            height=36,
            font=C.FONT_RIBBON,
            fg_color=C.STATUS_OK,
            hover_color="#27ae60",
            text_color="white",
            state="disabled",
            command=self._on_data_read_to_memory,
        )
        self.btn_data_read.pack(anchor="w", pady=(4, 12))

        self.label_data_result = ctk.CTkLabel(
            root,
            text="",
            font=C.FONT_BODY,
            text_color=C.GRAY_TEXT,
            justify="left",
            anchor="w",
        )
        self.label_data_result.pack(anchor="w", fill="x")

    def _set_data_entries_enabled(self, enabled: bool) -> None:
        st: str = "normal" if enabled else "disabled"
        self.entry_data_start.configure(state=st)
        self.entry_data_end.configure(state=st)
        self.entry_data_step.configure(state=st)
        self.btn_data_read.configure(state=st)

    def _set_entry_int(self, entry: Any, value: int) -> None:
        entry.delete(0, "end")
        entry.insert(0, str(int(value)))

    def _sync_data_entries_from_inputs(self) -> None:
        if self.total_traces <= 0:
            return
        try:
            start = int(self.entry_data_start.get()) if self.entry_data_start.get().strip() else 0
            end = int(self.entry_data_end.get()) if self.entry_data_end.get().strip() else self.total_traces
            step = int(self.entry_data_step.get()) if self.entry_data_step.get().strip() else 1
        except ValueError:
            return
        start = max(0, min(start, self.total_traces - 1))
        end = max(start + 1, min(end, self.total_traces))
        max_step = max(1, min(1000, max(1, self.total_traces // 10)))
        step = max(1, min(step, max_step))
        self._set_entry_int(self.entry_data_start, start)
        self._set_entry_int(self.entry_data_end, end)
        self._set_entry_int(self.entry_data_step, step)
        self._draw_home_selection_overlay(start, end)

    def _on_data_entries_focus_out(self, _event=None) -> None:
        self._sync_data_entries_from_inputs()

    def _home_trace_from_x(self, x_value: Any) -> Optional[int]:
        if x_value is None:
            return None
        try:
            x = float(x_value)
            x0, x1 = self._home_ax_before.get_xlim()
            if x1 == x0:
                return None
            ratio = (x - x0) / (x1 - x0)
            view_count = len(range(self._home_view_start, self._home_view_end, self._home_view_step))
            if view_count <= 0:
                return None
            local_idx = int(round(ratio * (view_count - 1)))
            local_idx = max(0, min(view_count - 1, local_idx))
            trace = self._home_view_start + local_idx * self._home_view_step
        except Exception:
            return None
        return max(0, min(self.total_traces - 1, trace))

    def _draw_home_selection_overlay(self, start: int, end: int) -> None:
        if not getattr(self, "_home_matplotlib_ok", False) or self.total_traces <= 0:
            return
        ax = self._home_ax_before
        if self._home_selection_patch is not None:
            try:
                self._home_selection_patch.remove()
            except Exception:
                pass
        x0, x1 = ax.get_xlim()
        if x1 == x0:
            return
        view_count = len(range(self._home_view_start, self._home_view_end, self._home_view_step))
        if view_count <= 0:
            return
        left_idx = (start - self._home_view_start) / max(1, self._home_view_step)
        right_idx = (end - self._home_view_start) / max(1, self._home_view_step)
        left_idx = max(0.0, min(float(view_count - 1), float(left_idx)))
        right_idx = max(0.0, min(float(view_count), float(right_idx)))
        left = x0 + (left_idx / max(1, view_count - 1)) * (x1 - x0)
        right = x0 + (right_idx / max(1, view_count - 1)) * (x1 - x0)
        self._home_selection_patch = ax.axvspan(min(left, right), max(left, right), color="#3a8dcc", alpha=0.25)
        self._home_canvas_before.draw_idle()

    def _on_home_before_press(self, event) -> None:
        if not getattr(self, "_home_matplotlib_ok", False) or event.inaxes is not self._home_ax_before:
            return
        tr = self._home_trace_from_x(event.xdata)
        if tr is None:
            return
        self._home_drag_anchor = tr
        self._apply_home_plot_selection(tr, tr)

    def _on_home_before_motion(self, event) -> None:
        if self._home_drag_anchor is None or event.inaxes is not self._home_ax_before:
            return
        tr = self._home_trace_from_x(event.xdata)
        if tr is None:
            return
        self._apply_home_plot_selection(self._home_drag_anchor, tr)

    def _on_home_before_release(self, event) -> None:
        if self._home_drag_anchor is None:
            return
        tr = self._home_trace_from_x(event.xdata)
        if tr is None:
            tr = self._home_drag_anchor
        self._apply_home_plot_selection(self._home_drag_anchor, tr)
        self._home_drag_anchor = None

    def _apply_home_plot_selection(self, trace_a: int, trace_b: int) -> None:
        left = min(trace_a, trace_b)
        right = max(trace_a, trace_b)
        left = max(self._home_view_start, left)
        right = min(self._home_view_end - 1, right)
        if right < left:
            right = left
        self._set_entry_int(self.entry_data_start, left)
        self._set_entry_int(self.entry_data_end, min(self.total_traces, right + 1))
        self._draw_home_selection_overlay(left, min(self.total_traces, right + 1))

    def _sync_data_tab_after_load(self) -> None:
        for e in (self.entry_data_start, self.entry_data_end, self.entry_data_step):
            e.configure(state="normal")
            e.delete(0, "end")
        self.label_data_result.configure(text="")
        if self.total_traces <= 0 or not self.current_file_path:
            self.label_data_meta.configure(
                text="Метаданные SEG-Y недоступны (проверьте файл и segyio).",
                text_color=C.STATUS_WARN,
            )
            self._set_data_entries_enabled(False)
            return
        self.label_data_meta.configure(
            text=f"Всего трасс: {self.total_traces}\nОтсчётов на трассу: {self.samples_count}",
            text_color=C.GRAY_TEXT,
        )
        self._set_data_entries_enabled(True)
        self.entry_data_end.configure(placeholder_text=str(self.total_traces))
        self._set_entry_int(self.entry_data_start, 0)
        self._set_entry_int(self.entry_data_end, self.total_traces)
        self._set_entry_int(self.entry_data_step, 1)
        self._home_view_start = 0
        self._home_view_end = self.total_traces
        self._home_view_step = 1
        self._sync_data_entries_from_inputs()

    def _reset_data_tab_state(self) -> None:
        self.total_traces = 0
        self.samples_count = 0
        self.matrix_data = None
        for e in (self.entry_data_start, self.entry_data_end, self.entry_data_step):
            e.configure(state="normal")
            e.delete(0, "end")
        self.label_data_meta.configure(
            text="Файл не загружен — сначала откройте SEG-Y на вкладке «Файл».",
            text_color=C.GRAY_TEXT_MUTED,
        )
        self.label_data_result.configure(text="")
        self._set_data_entries_enabled(False)
        self.entry_data_end.configure(placeholder_text="—")
        self._home_view_start = 0
        self._home_view_end = 0
        self._home_view_step = 1

    def _on_data_read_to_memory(self) -> None:
        if not self.current_file_path or self.total_traces <= 0:
            return
        try:
            start = int(self.entry_data_start.get()) if self.entry_data_start.get().strip() else 0
            end = int(self.entry_data_end.get()) if self.entry_data_end.get().strip() else self.total_traces
            step = int(self.entry_data_step.get()) if self.entry_data_step.get().strip() else 1
        except ValueError:
            self.label_data_result.configure(text="Ошибка: введите целые числа в поля От / До / Шаг.", text_color=C.STATUS_WARN)
            return

        if step <= 0:
            self.label_data_result.configure(text="Ошибка: шаг должен быть больше 0.", text_color=C.STATUS_WARN)
            return
        if start < 0 or end > self.total_traces or start >= end:
            self.label_data_result.configure(
                text=f"Ошибка: диапазон трасс 0 … {self.total_traces}, нужно От < До.",
                text_color=C.STATUS_WARN,
            )
            return

        try:
            import segyio
            import numpy as np

            self.label_data_result.configure(text="Чтение данных…", text_color=C.STATUS_PENDING)
            self.update_idletasks()

            chunk_size = 5000
            max_full_matrix_bytes = 512 * 1024 * 1024
            selected_traces = len(range(start, end, step))
            if selected_traces <= 0:
                self.label_data_result.configure(text="Ошибка: выбран пустой диапазон.", text_color=C.STATUS_WARN)
                return

            with segyio.open(self.current_file_path, "r", ignore_geometry=True, strict=False) as f:
                n_samples = int(len(f.samples))
                est_bytes = selected_traces * n_samples * 4
                keep_full_matrix = est_bytes <= max_full_matrix_bytes

                full_chunks: list[np.ndarray] = []
                max_abs = 0.0

                # Для больших диапазонов собираем только прореженное превью,
                # чтобы не хранить всю матрицу в RAM.
                preview_target = min(512, selected_traces)
                preview_idx = (
                    np.linspace(0, selected_traces - 1, num=preview_target, dtype=np.int64)
                    if preview_target > 0
                    else np.empty((0,), dtype=np.int64)
                )
                preview_cursor = 0
                preview_rows: list[np.ndarray] = []

                for offset in range(0, selected_traces, chunk_size):
                    part_end = min(offset + chunk_size, selected_traces)
                    from_trace = start + offset * step
                    to_trace = start + part_end * step
                    chunk_matrix = np.asarray(f.trace.raw[from_trace:to_trace:step], dtype=np.float32)
                    if chunk_matrix.ndim != 2 or chunk_matrix.size == 0:
                        continue

                    chunk_max = float(np.max(np.abs(chunk_matrix)))
                    if chunk_max > max_abs:
                        max_abs = chunk_max

                    if keep_full_matrix:
                        full_chunks.append(chunk_matrix)

                    while preview_cursor < len(preview_idx) and int(preview_idx[preview_cursor]) < part_end:
                        local_row = int(preview_idx[preview_cursor]) - offset
                        if 0 <= local_row < chunk_matrix.shape[0]:
                            preview_rows.append(chunk_matrix[local_row : local_row + 1, :])
                        preview_cursor += 1

                if keep_full_matrix:
                    self.matrix_data = np.concatenate(full_chunks, axis=0) if full_chunks else np.empty((0, 0), dtype=np.float32)
                    plot_matrix = self.matrix_data
                    mode_msg = "Данные в памяти"
                else:
                    self.matrix_data = None
                    plot_matrix = np.concatenate(preview_rows, axis=0) if preview_rows else np.empty((0, 0), dtype=np.float32)
                    mode_msg = "Потоковый режим (без полной загрузки в RAM)"

            shape = plot_matrix.shape
            msg = (
                f"{mode_msg}: матрица {shape[0]}×{shape[1]} "
                f"(трассы {start}:{end}:{step}), max|A|={max_abs:.3g}."
            )
            self._home_view_start = start
            self._home_view_end = end
            self._home_view_step = step
            self.label_data_result.configure(text=msg, text_color=C.STATUS_OK)
            self._update_home_before_from_matrix(plot_matrix)
        except Exception as ex:
            self.matrix_data = None
            self.label_data_result.configure(text=f"Ошибка чтения: {ex}", text_color=C.STATUS_WARN)

    def _update_home_before_from_matrix(self, matrix: Any) -> None:
        """Обновить график «До» по полной матрице (после «Выбрать данные»)."""
        if not getattr(self, "_home_matplotlib_ok", False):
            self.label_data_result.configure(
                text="Данные считаны, но Matplotlib недоступен для отрисовки.",
                text_color=C.STATUS_WARN,
            )
            return
        import numpy as np

        try:
            arr = np.asarray(matrix, dtype=np.float32)
        except Exception:
            # Для некоторых реализаций segyio может прийти массив объектов.
            try:
                rows = [np.asarray(r, dtype=np.float32) for r in matrix]
                arr = np.stack(rows, axis=0) if rows else np.empty((0, 0), dtype=np.float32)
            except Exception:
                arr = np.empty((0, 0), dtype=np.float32)

        if arr.ndim != 2 or arr.size == 0:
            self.label_data_result.configure(
                text="Данные считаны, но не удалось получить 2D-матрицу для графика.",
                text_color=C.STATUS_WARN,
            )
            return
        flat = np.abs(arr).ravel()
        p98 = float(np.percentile(flat, 98.0)) if flat.size else 1.0
        if p98 <= 0.0:
            p98 = 1.0
        norm = np.clip(arr / p98, -1.0, 1.0)

        axb = self._home_ax_before
        axb.clear()
        axb.axis("on")
        axb.imshow(
            norm.T,
            aspect="auto",
            cmap="gray",
            vmin=-1.0,
            vmax=1.0,
            interpolation="bilinear",
            origin="upper",
        )
        axb.set_xlabel("Трасса (выбранный диапазон)")
        axb.set_ylabel("Время / отсчёт")
        axb.tick_params(labelsize=8)
        self._home_fig_before.subplots_adjust(left=0.11, right=0.99, top=0.94, bottom=0.14)
        self._home_canvas_before.draw()
        self.update_idletasks()

        self._home_apply_placeholder(self._home_ax_after, "Здесь будет превью после обработки.")
        self._home_canvas_after.draw()
        self.after(10, self._home_refresh_matplotlib_geometry)
        self.after(200, self._home_refresh_matplotlib_geometry)

    def _home_matplotlib_host_bg(self) -> str:
        try:
            if ctk.get_appearance_mode() == "Dark":
                return str(C.ANALYSIS_WORKSPACE_INNER[1])
            return str(C.ANALYSIS_WORKSPACE_INNER[0])
        except Exception:
            return "#d8d8d8"

    def _home_refresh_matplotlib_geometry(self) -> None:
        """Matplotlib + CustomTkinter: canvas часто 0×0, пока не подогнать fig под виджет и не draw()."""
        if not getattr(self, "_home_matplotlib_ok", False):
            return
        pairs = (
            (self._home_canvas_before, self._home_fig_before),
            (self._home_canvas_after, self._home_fig_after),
        )
        for canvas, fig in pairs:
            if canvas is None or fig is None:
                continue
            tw = canvas.get_tk_widget()
            try:
                self.update_idletasks()
                w = int(tw.winfo_width())
                h = int(tw.winfo_height())
            except tk.TclError:
                continue
            if w > 24 and h > 24:
                w = min(w, 3200)
                h = min(h, 2400)
                fig.set_size_inches(w / fig.get_dpi(), h / fig.get_dpi(), forward=False)
            try:
                canvas.draw()
            except Exception:
                pass

    def setup_home_page(self) -> None:
        """Вкладка «Главная»: две колонки «До» / «После»; «До» — превью загруженного SEG-Y."""
        f = self.frames["Главная"]
        outer = ctk.CTkFrame(f, fg_color="transparent")
        outer.pack(fill="both", expand=True, padx=10, pady=10)
        outer.grid_columnconfigure(0, weight=1, uniform="home")
        outer.grid_columnconfigure(1, weight=1, uniform="home")
        outer.grid_rowconfigure(0, weight=1)

        def pane(col: int, title: str, pad_l: int, pad_r: int) -> ctk.CTkFrame:
            box = ctk.CTkFrame(
                outer,
                fg_color=C.PIPELINE_CARD_FG,
                corner_radius=C.RIBBON_CORNER_RADIUS,
                border_width=1,
                border_color=C.PIPELINE_CARD_BORDER,
            )
            box.grid(row=0, column=col, sticky="nsew", padx=(pad_l, pad_r))
            box.grid_rowconfigure(1, weight=1)
            box.grid_columnconfigure(0, weight=1)
            ctk.CTkLabel(
                box,
                text=title,
                font=C.FONT_HEAD,
                text_color=C.GRAY_TEXT,
                anchor="w",
            ).grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 6))
            host = ctk.CTkFrame(box, fg_color=C.ANALYSIS_WORKSPACE_INNER, corner_radius=8)
            host.grid(row=1, column=0, sticky="nsew", padx=10, pady=(0, 10))
            return host

        self._home_plot_host_before = pane(0, "До", 0, 6)
        self._home_plot_host_after = pane(1, "После", 6, 0)
        self._home_matplotlib_ok = False
        self._home_fig_before = None
        self._home_ax_before = None
        self._home_canvas_before = None
        self._home_fig_after = None
        self._home_ax_after = None
        self._home_canvas_after = None

        try:
            from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg
            from matplotlib.figure import Figure

            def mount(host: ctk.CTkFrame, placeholder: str) -> tuple[Any, Any, Any]:
                # Обычный tk.Frame — у CTkFrame дочерний Matplotlib часто не получает размер (холст 0 px).
                inner = tk.Frame(host, bg=self._home_matplotlib_host_bg(), highlightthickness=0, bd=0)
                inner.pack(fill="both", expand=True)
                fig = Figure(figsize=(5.0, 4.0), dpi=100)
                fig.patch.set_facecolor("#ececec")
                ax = fig.add_subplot(111)
                ax.set_facecolor("#e4e4e4")
                ax.axis("off")
                ax.text(
                    0.5,
                    0.5,
                    placeholder,
                    ha="center",
                    va="center",
                    transform=ax.transAxes,
                    fontsize=11,
                    color="#555555",
                )
                canvas = FigureCanvasTkAgg(fig, master=inner)
                canvas.get_tk_widget().pack(fill="both", expand=True)
                return fig, ax, canvas

            self._home_fig_before, self._home_ax_before, self._home_canvas_before = mount(
                self._home_plot_host_before,
                "Загрузите файл на вкладке «Файл».",
            )
            self._home_canvas_before.mpl_connect("button_press_event", self._on_home_before_press)
            self._home_canvas_before.mpl_connect("motion_notify_event", self._on_home_before_motion)
            self._home_canvas_before.mpl_connect("button_release_event", self._on_home_before_release)
            self._home_fig_after, self._home_ax_after, self._home_canvas_after = mount(
                self._home_plot_host_after,
                "Здесь будет превью после обработки.",
            )
            self._home_matplotlib_ok = True
        except Exception:
            ctk.CTkLabel(
                self._home_plot_host_before,
                text="Для графиков: pip install matplotlib numpy segyio",
                font=C.FONT_SMALL,
                text_color=C.GRAY_TEXT_MUTED,
                wraplength=220,
            ).pack(expand=True, padx=12, pady=24)
            ctk.CTkLabel(
                self._home_plot_host_after,
                text="Панель «После»",
                font=C.FONT_SMALL,
                text_color=C.GRAY_TEXT_MUTED,
            ).pack(expand=True, padx=12, pady=24)

    def _home_apply_placeholder(self, ax: Any, text: str) -> None:
        ax.clear()
        ax.set_facecolor("#e4e4e4")
        ax.axis("off")
        ax.text(
            0.5,
            0.5,
            text,
            ha="center",
            va="center",
            transform=ax.transAxes,
            fontsize=11,
            color="#555555",
        )

    def _reset_home_plots_empty(self) -> None:
        if not getattr(self, "_home_matplotlib_ok", False):
            return
        self._home_apply_placeholder(self._home_ax_before, "Загрузите файл на вкладке «Файл».")
        self._home_selection_patch = None
        self._home_apply_placeholder(self._home_ax_after, "Здесь будет превью после обработки.")
        self._home_canvas_before.draw()
        self._home_canvas_after.draw()
        self.after(50, self._home_refresh_matplotlib_geometry)

    def _update_home_plots_after_load(self, preview: Optional[SeismicPreview]) -> None:
        if not getattr(self, "_home_matplotlib_ok", False):
            return
        if preview is None and self.current_file_path:
            try:
                from logic.seismic import load_segy_preview

                preview = load_segy_preview(self.current_file_path)
            except Exception:
                preview = None
        axb = self._home_ax_before
        axb.clear()
        axb.axis("on")
        if preview is None:
            self._home_apply_placeholder(
                axb,
                "Файл принят, но превью SEG-Y недоступно\n(установите segyio/numpy или проверьте файл).",
            )
        else:
            import numpy as np

            try:
                arr = np.frombuffer(preview.data, dtype=np.float32).reshape(
                    preview.n_traces, preview.n_samples
                )
            except ValueError:
                self._home_apply_placeholder(
                    axb,
                    "Превью повреждено (размер буфера не совпадает с формой).",
                )
            else:
                axb.imshow(
                    arr.T,
                    aspect="auto",
                    cmap="gray",
                    vmin=-1.0,
                    vmax=1.0,
                    interpolation="bilinear",
                    origin="upper",
                )
                axb.set_xlabel("Трасса (прорежено)")
                axb.set_ylabel("Время / отсчёт")
                axb.tick_params(labelsize=8)

        self._home_fig_before.subplots_adjust(left=0.11, right=0.99, top=0.94, bottom=0.14)
        if self.total_traces > 0:
            s = int(self.entry_data_start.get()) if self.entry_data_start.get().strip() else 0
            e = int(self.entry_data_end.get()) if self.entry_data_end.get().strip() else self.total_traces
            self._draw_home_selection_overlay(s, e)
        self._home_canvas_before.draw()
        self.update_idletasks()
        self.after(10, self._home_refresh_matplotlib_geometry)
        self.after(200, self._home_refresh_matplotlib_geometry)

        self._home_apply_placeholder(self._home_ax_after, "Здесь будет превью после обработки.")
        self._home_canvas_after.draw()

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
        if name == "Главная":
            self.after(80, self._home_refresh_matplotlib_geometry)

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
