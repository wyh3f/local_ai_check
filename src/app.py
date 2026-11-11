"""本地 AI 部署能力检测工具（customtkinter 浅色专业风界面）。

用法：
    python app.py                启动图形界面
    python app.py --cli          在命令行直接输出检测报告
    python app.py --selftest [路径]  运行可移植性自检，结果写入 JSON
"""

from __future__ import annotations

import math
import os
import platform
import sys
import threading
import time
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk

from appinfo import APP_TITLE, VERSION
from evaluator import TIERS, Evaluation, evaluate
from hardware import HardwareInfo, detect_hardware
from platform_win import asset_path, grab_window_rgb, system_dpi, work_area, write_png
from report import build_report, gpu_line, run_cli
from selftest import run_selftest

# 浅色专业风配色
BG = "#f4f6f9"
CARD = "#ffffff"
BORDER = "#e3e8ef"
PRIMARY = "#2563eb"
PRIMARY_HOVER = "#1d4ed8"
PRIMARY_DISABLED = "#9db9f5"
TEXT = "#1f2937"
MUTED = "#6b7280"
SKELETON = "#cbd5e1"
SKELETON_HOT = "#94a3b8"
OK = "#15803d"
WARN = "#c2410c"
BAD = "#b91c1c"

STATUS_TEXTS = (
    "正在读取 CPU 信息…（1/4）",
    "正在识别显卡与显存…（2/4）",
    "正在检查内存与磁盘…（3/4）",
    "正在评估模型等级…（4/4）",
)


class _SelectableText(tk.Text):
    """只读文本块：支持鼠标拖选与 Ctrl+C 复制，高度随换行自动伸缩。"""

    def __init__(self, master, font, fg: str = TEXT, bg: str = CARD) -> None:
        super().__init__(
            master,
            height=1,
            width=1,
            wrap="word",
            bd=0,
            highlightthickness=0,
            bg=bg,
            fg=fg,
            font=font,
            cursor="xterm",
            padx=0,
            pady=0,
            insertwidth=0,
            selectbackground="#bfdbfe",
            selectforeground=TEXT,
            takefocus=0,
        )
        self.configure(state="disabled")
        self.bind("<Configure>", self._autosize)

    def set_text(self, text: str, color: str | None = None) -> None:
        # 内容未变时只更新颜色，避免打断用户正在进行的拖选
        if self.get("1.0", "end-1c") != text:
            self.configure(state="normal")
            self.delete("1.0", "end")
            self.insert("1.0", text)
            self.configure(state="disabled")
            self._autosize()
        if color:
            self.configure(fg=color)

    def _autosize(self, event: tk.Event | None = None) -> None:
        width = (event.width if event is not None else self.winfo_width()) - 4
        if width <= 20:
            return
        font = self.cget("font")
        lines = 0
        for line in self.get("1.0", "end-1c").split("\n"):
            if not line:
                lines += 1
                continue
            pixels = self.tk.call("font", "measure", font, line)
            lines += max(1, math.ceil(pixels / width))
        lines = max(1, lines)
        if int(self.cget("height")) != lines:
            self.configure(height=lines)


class App:
    """图形界面主窗口。"""

    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.report = ""
        self.hw: HardwareInfo | None = None
        self.busy = False
        self._score_after: str | None = None
        self._status_after: str | None = None
        self._status_index = 0
        self._score_value = 0
        self._spinner_after: str | None = None
        self._spinner_index = 0
        self._skeleton_after: str | None = None
        self._skeleton_index = 0
        self._progress_after: str | None = None
        self._progress_value = 0.0
        self._progress_target = 0.0

        root.title(f"{APP_TITLE} v{VERSION}")
        try:
            root.iconbitmap(asset_path("icon.ico"))
        except Exception:
            pass

        # customtkinter 自己处理 DPI 缩放，geometry 传入的是逻辑像素
        self.scale = root._get_window_scaling()
        wx, wy, ww, wh = work_area()
        if not ww or not wh:
            wx, wy, ww, wh = 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()
        logical_w, logical_h = ww / self.scale, wh / self.scale
        width = int(min(1000, max(780, logical_w - 80)))
        height = int(min(880, max(560, logical_h - 100)))
        x = int(wx / self.scale + max(0, (logical_w - width) / 2))
        y = int(wy / self.scale + max(0, (logical_h - height) / 2))
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.minsize(760, 520)

        self._init_style()
        self._build_layout()
        self.refresh()

    # ---------- 样式 ----------
    def _font(self, size: int, weight: str = "normal") -> ctk.CTkFont:
        return ctk.CTkFont(family="Microsoft YaHei", size=size, weight=weight)

    def _init_style(self) -> None:
        """ttk 表格不归 customtkinter 管，需要手动贴合主题。"""
        style = ttk.Style()
        style.theme_use("clam")
        # ttk 字号按磅计算，逻辑像素 × 0.75 换算后与 CTk 字号视觉一致
        point_size = max(8, round(13 * 0.75))
        style.configure(
            "Tiers.Treeview",
            background=CARD,
            fieldbackground=CARD,
            foreground=TEXT,
            borderwidth=0,
            rowheight=int(28 * self.scale),
            font=("Microsoft YaHei", point_size),
        )
        style.configure(
            "Tiers.Treeview.Heading",
            background="#f1f5f9",
            foreground="#475569",
            relief="flat",
            borderwidth=0,
            font=("Microsoft YaHei", point_size, "bold"),
        )
        style.map("Tiers.Treeview.Heading", background=[("active", "#e2e8f0")])
        style.map(
            "Tiers.Treeview",
            background=[("selected", "#dbeafe")],
            foreground=[("selected", TEXT)],
        )
        style.layout("Tiers.Treeview", [("Tiers.Treeview.treearea", {"sticky": "nswe"})])

    # ---------- 界面搭建 ----------
    def _build_layout(self) -> None:
        self._build_action_bar()
        body = self._build_scroll_area()
        self._build_header(body)
        self._build_summary_card(body)
        self._build_tier_table(body)
        self._build_info_row(body)
        self._build_advice_card(body)

    def _build_action_bar(self) -> None:
        """底部操作栏固定在窗口底部，不随内容滚动。"""
        bar = ctk.CTkFrame(self.root, fg_color=CARD, corner_radius=0, height=64, border_width=0)
        bar.pack(side="bottom", fill="x")
        bar.pack_propagate(False)
        self.refresh_btn = ctk.CTkButton(
            bar,
            text="重新检测",
            width=112,
            height=34,
            corner_radius=8,
            fg_color=PRIMARY,
            hover_color=PRIMARY_HOVER,
            font=self._font(13, "bold"),
            command=self.refresh,
        )
        self.refresh_btn.pack(side="left", padx=(20, 8), pady=15)
        self._secondary_button(bar, "复制报告", self.copy_report)
        self._secondary_button(bar, "保存报告", self.save_report)
        self._secondary_button(bar, "关于", self.show_about)

        self.status_var = tk.StringVar(value="准备就绪")
        ctk.CTkLabel(
            bar, textvariable=self.status_var, font=self._font(12), text_color=MUTED
        ).pack(side="right", padx=(8, 20))
        self.progress = ctk.CTkProgressBar(
            bar, mode="indeterminate", width=110, height=6, progress_color=PRIMARY, fg_color="#e2e8f0"
        )
        self.progress.set(0)

    def _secondary_button(self, parent, text: str, command) -> ctk.CTkButton:
        button = ctk.CTkButton(
            parent,
            text=text,
            width=96,
            height=34,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color="#d1d5db",
            text_color="#374151",
            hover_color="#f3f4f6",
            font=self._font(13),
            command=command,
        )
        button.pack(side="left", padx=(0, 8), pady=15)
        return button

    def _build_scroll_area(self) -> ctk.CTkScrollableFrame:
        body = ctk.CTkScrollableFrame(
            self.root,
            fg_color=BG,
            corner_radius=0,
            scrollbar_button_color="#cbd5e1",
            scrollbar_button_hover_color="#94a3b8",
        )
        body.pack(fill="both", expand=True)
        self.body = body
        return body

    def _card(self, body: ctk.CTkFrame, pady: tuple[int, int] = (0, 14)) -> ctk.CTkFrame:
        card = ctk.CTkFrame(body, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        card.pack(fill="x", padx=22, pady=pady)
        return card

    def _build_header(self, body: ctk.CTkFrame) -> None:
        ctk.CTkLabel(
            body, text=APP_TITLE, font=self._font(26, "bold"), text_color=TEXT, anchor="w"
        ).pack(fill="x", padx=22, pady=(20, 2))
        ctk.CTkLabel(
            body,
            text="检测本机 CPU、内存、显卡与磁盘，评估能否在本地运行开源大模型",
            font=self._font(12),
            text_color=MUTED,
            anchor="w",
        ).pack(fill="x", padx=22, pady=(0, 14))

    def _build_summary_card(self, body: ctk.CTkFrame) -> None:
        """结论卡片：结论、等级、推理路径、资源池、评分与用途标签。"""
        card = self._card(body, pady=(0, 14))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=18)

        self.headline = _SelectableText(inner, font=self._font(20, "bold"), fg=MUTED)
        self.headline.pack(fill="x")
        self.headline.set_text("正在检测…")

        meta = ctk.CTkFrame(inner, fg_color="transparent")
        meta.pack(fill="x", pady=(10, 0))
        self.tier_label = self._meta_label(meta, "等级：—")
        self.path_label = self._meta_label(meta, "推理路径：—")
        self.budget_label = self._meta_label(meta, "资源池：—")
        self.score_label = self._meta_label(meta, "综合评分：—")

        row = ctk.CTkFrame(inner, fg_color="transparent")
        row.pack(fill="x", pady=(12, 0))
        self.tag_chips: list[tuple[ctk.CTkFrame, ctk.CTkLabel]] = []
        for _ in range(4):
            chip = ctk.CTkFrame(row, fg_color="#eef2f7", corner_radius=9)
            chip.pack(side="left", padx=(0, 8))
            label = ctk.CTkLabel(chip, text="检测中", font=self._font(12), text_color=SKELETON_HOT)
            label.pack(padx=10, pady=3)
            self.tag_chips.append((chip, label))

    def _meta_label(self, parent: ctk.CTkFrame, text: str) -> ctk.CTkLabel:
        label = ctk.CTkLabel(parent, text=text, font=self._font(12), text_color=MUTED, anchor="w")
        label.pack(side="left", padx=(0, 26))
        return label

    def _build_info_row(self, body: ctk.CTkFrame) -> None:
        """系统信息与硬件信息左右并排，给等级对照表留出首屏空间。"""
        row = ctk.CTkFrame(body, fg_color="transparent")
        row.pack(fill="x", padx=22, pady=(0, 12))
        row.grid_columnconfigure((0, 1), weight=1, uniform="info")

        system = ctk.CTkFrame(row, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        system.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        hardware = ctk.CTkFrame(row, fg_color=CARD, corner_radius=14, border_width=1, border_color=BORDER)
        hardware.grid(row=0, column=1, sticky="nsew", padx=(6, 0))

        self.sys_labels = self._fill_info_card(
            system,
            "系统信息",
            [
                ("os", "操作系统"),
                ("arch", "系统架构"),
                ("host", "计算机名"),
                ("user", "当前用户"),
                ("mem", "内存使用"),
            ],
        )
        self.hw_labels = self._fill_info_card(
            hardware,
            "硬件信息",
            [("cpu", "CPU"), ("gpu", "显卡"), ("ram", "内存"), ("disk", "磁盘")],
        )

    def _fill_info_card(
        self, card: ctk.CTkFrame, title: str, rows: list[tuple[str, str]]
    ) -> dict[str, _SelectableText]:
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="both", expand=True, padx=18, pady=(14, 14))
        ctk.CTkLabel(
            inner, text=title, font=self._font(14, "bold"), text_color="#334155", anchor="w"
        ).pack(fill="x", pady=(0, 8))

        values: dict[str, _SelectableText] = {}
        for key, label in rows:
            row = ctk.CTkFrame(inner, fg_color="transparent")
            row.pack(fill="x", pady=2)
            ctk.CTkLabel(
                row, text=label, width=76, font=self._font(12), text_color=MUTED, anchor="w"
            ).pack(side="left", anchor="n")
            value = _SelectableText(row, font=self._font(12), fg=TEXT)
            value.pack(side="left", fill="x", expand=True)
            value.set_text("—")
            values[key] = value

        return values

    def _build_tier_table(self, body: ctk.CTkFrame) -> None:
        """等级对照表，列宽按窗口宽度比例自适应。"""
        card = self._card(body)
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=(14, 16))
        ctk.CTkLabel(
            inner,
            text="等级对照（按 4-bit 量化估算）",
            font=self._font(14, "bold"),
            text_color="#334155",
            anchor="w",
        ).pack(fill="x", pady=(0, 8))

        columns = ("tier", "params", "need", "verdict", "reason", "speed", "scene")
        self.tree = ttk.Treeview(
            inner, columns=columns, show="headings", height=6, style="Tiers.Treeview"
        )
        headings = {
            "tier": ("等级", 78),
            "params": ("参数量级", 85),
            "need": ("4-bit 占用", 85),
            "verdict": ("结论", 60),
            "reason": ("判定依据", 140),
            "speed": ("预估速度", 115),
            "scene": ("适用场景", 280),
        }
        for key, (text, width) in headings.items():
            self.tree.heading(key, text=text)
            self.tree.column(key, width=int(width * self.scale), anchor="w" if key == "scene" else "center")

        # 背景斑马纹 + 前景按结论着色，组合成 8 个 tag
        for name, color in (("ok", OK), ("usable", "#b45309"), ("tight", WARN), ("no", "#9ca3af")):
            for parity, background in (("odd", "#f8fafc"), ("even", CARD)):
                self.tree.tag_configure(f"{name}_{parity}", foreground=color, background=background)
        # 加载态骨架：灰字占位 + 逐行高亮的扫描波
        self.tree.tag_configure("skel", foreground=SKELETON, background="#f8fafc")
        self.tree.tag_configure("skel_even", foreground=SKELETON, background=CARD)
        self.tree.tag_configure("skel_hot", foreground=SKELETON_HOT, background="#e8f0fe")
        self.tree.pack(fill="x")
        self.tree.bind("<Control-c>", self.copy_tree_selection)
        self._fill_skeleton()

        self._col_weights = {
            "tier": 0.08,
            "params": 0.09,
            "need": 0.09,
            "verdict": 0.07,
            "reason": 0.18,
            "speed": 0.16,
            "scene": 0.33,
        }
        self._tree_width = 0
        self.tree.bind("<Configure>", self._fit_tree_columns)

    def _build_advice_card(self, body: ctk.CTkFrame) -> None:
        card = self._card(body, pady=(0, 18))
        inner = ctk.CTkFrame(card, fg_color="transparent")
        inner.pack(fill="x", padx=20, pady=(14, 16))
        ctk.CTkLabel(
            inner,
            text="这台电脑用 AI 能做什么",
            font=self._font(14, "bold"),
            text_color="#334155",
            anchor="w",
        ).pack(fill="x", pady=(0, 8))
        self.advice_text = ctk.CTkTextbox(
            inner,
            height=170,
            fg_color="transparent",
            border_width=0,
            corner_radius=0,
            font=self._font(12),
            text_color=TEXT,
            wrap="word",
        )
        self.advice_text.pack(fill="x")
        self.advice_text.configure(state="disabled")

    # ---------- 检测流程 ----------
    def _fit_tree_columns(self, event: tk.Event) -> None:
        width = event.width - int(6 * self.scale)
        if width <= 0 or abs(width - self._tree_width) < 6:
            return
        self._tree_width = width
        minimum = int(48 * self.scale)
        for key, ratio in self._col_weights.items():
            self.tree.column(key, width=max(minimum, int(width * ratio)))

    def refresh(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.refresh_btn.configure(
            state="disabled", fg_color=PRIMARY_DISABLED, hover_color=PRIMARY_DISABLED
        )
        self.progress.pack(side="right", padx=(8, 6))
        self.progress.configure(mode="determinate")
        self.progress.set(0)
        self._progress_value = 0.0
        self._progress_target = 0.12
        self.score_label.configure(text="综合评分：—")
        self._fill_skeleton()
        self._start_spinner()
        self._start_skeleton_wave()
        self._start_status_rotation()
        self._start_progress()
        threading.Thread(target=self._detect_worker, daemon=True).start()

    def _detect_worker(self) -> None:
        started = time.perf_counter()
        try:
            hw = detect_hardware()
            ev = evaluate(hw)
            self.root.after(0, self._render, hw, ev, time.perf_counter() - started, None)
        except Exception as exc:  # 界面上直接反馈异常，避免静默失败
            self.root.after(0, self._render, None, None, 0.0, exc)

    def _render(
        self,
        hw: HardwareInfo | None,
        ev: Evaluation | None,
        elapsed: float,
        error: Exception | None,
    ) -> None:
        self.busy = False
        self.hw = hw
        self._stop_loading()
        self.refresh_btn.configure(state="normal", fg_color=PRIMARY, hover_color=PRIMARY_HOVER)
        if error or hw is None or ev is None:
            self.headline.set_text("检测失败", BAD)
            self.status_var.set(f"错误：{error}")
            for chip, label in self.tag_chips:
                chip.configure(fg_color="#f3f4f6")
                label.configure(text="—", text_color=MUTED)
            return

        self.report = build_report(hw, ev)
        self._render_summary(ev)
        self._render_system(hw)
        self._render_hardware(hw)
        self._render_tiers(ev)
        self._render_advice(ev)
        self._animate_score(ev.score)
        self.status_var.set(f"检测完成，耗时 {elapsed:.1f} 秒")

    def _render_summary(self, ev: Evaluation) -> None:
        if not ev.deployable:
            color = BAD
        elif any(t.verdict == "推荐" for t in ev.tiers):
            color = OK
        else:
            # 能跑但都只是「勉强」级别，用橙色提醒
            color = WARN
        self.headline.set_text(ev.headline, color)

        self.tier_label.configure(text=f"等级：{ev.tier}")
        self.path_label.configure(text=f"推理路径：{ev.path}")
        self.budget_label.configure(
            text=f"资源池：{ev.pool_label} {ev.pool_gb:g} GB（推荐线 {ev.rec_line:g}%）"
        )
        self.score_label.configure(text="综合评分：0/100")

        styles = {
            "✔": ("#e7f6ec", OK),
            "△": ("#fdf3e3", "#b45309"),
            "✘": ("#f3f4f6", "#9ca3af"),
        }
        for (chip, label), (name, mark) in zip(self.tag_chips, ev.usage_tags):
            background, foreground = styles.get(mark, ("#f3f4f6", MUTED))
            chip.configure(fg_color=background)
            label.configure(text=f"{name} {mark}", text_color=foreground)

    def _render_system(self, hw: HardwareInfo) -> None:
        self.sys_labels["os"].set_text(hw.os_name)
        self.sys_labels["arch"].set_text(hw.arch)
        self.sys_labels["host"].set_text(hw.hostname)
        self.sys_labels["user"].set_text(hw.username)
        self.sys_labels["mem"].set_text(
            f"{hw.ram_percent}%（可用 {hw.ram_available_gb:g} GB / 共 {hw.ram_total_gb:g} GB）"
        )

    def _render_hardware(self, hw: HardwareInfo) -> None:
        cpu_text = f"{hw.cpu_name}\n{hw.cpu_cores} 核 {hw.cpu_threads} 线程 @ {hw.cpu_freq_mhz} MHz"
        if hw.cpu_usage >= 0:
            cpu_text += f"，当前占用 {hw.cpu_usage}%"
        self.hw_labels["cpu"].set_text(cpu_text)

        self.hw_labels["gpu"].set_text("\n".join(gpu_line(g) for g in hw.gpus) or "未检测到显卡")

        ram_text = f"{hw.ram_total_gb:g} GB（可用 {hw.ram_available_gb:g} GB"
        if hw.ram_speed_mhz:
            ram_text += f"，{hw.ram_speed_mhz} MHz"
        self.hw_labels["ram"].set_text(ram_text + "）")

        disk_text = f"{hw.disk_free_gb:g} GB 可用 / 共 {hw.disk_total_gb:g} GB"
        if hw.disk_model:
            disk_text = f"{hw.disk_model}，{disk_text}"
        self.hw_labels["disk"].set_text(disk_text)

    def _render_tiers(self, ev: Evaluation) -> None:
        self.tree.delete(*self.tree.get_children())
        for index, t in enumerate(ev.tiers):
            name = {"推荐": "ok", "可用": "usable", "勉强": "tight"}.get(t.verdict, "no")
            parity = "even" if index % 2 == 0 else "odd"
            speed_text = "跑不动" if t.speed == "不可用" else f"{t.speed} tok/s（{t.speed_level}）"
            self.tree.insert(
                "",
                "end",
                values=(
                    t.label,
                    t.params_desc,
                    f"{t.need_gb:g} GB",
                    t.verdict,
                    t.reason,
                    speed_text,
                    t.scene,
                ),
                tags=(f"{name}_{parity}",),
            )

    def _render_advice(self, ev: Evaluation) -> None:
        self.advice_text.configure(state="normal")
        self.advice_text.delete("1.0", "end")
        for idx, tip in enumerate(ev.advice, 1):
            self.advice_text.insert("end", f"{idx}. {tip}\n")
        self.advice_text.configure(state="disabled")

    # ---------- 加载态 ----------
    def _fill_skeleton(self) -> None:
        """预填 6 行骨架，避免检测期间表格大片空白。"""
        self.tree.delete(*self.tree.get_children())
        for index, tier in enumerate(TIERS):
            tag = "skel_even" if index % 2 == 0 else "skel"
            self.tree.insert(
                "",
                "end",
                values=(tier[0], tier[1], "· · ·", "· · ·", "· · ·", "· · ·", "· · ·"),
                tags=(tag,),
            )

    def _start_spinner(self) -> None:
        """检测中的活性指示：圆点颜色在蓝与浅蓝间脉冲。"""
        self._spinner_index = 0

        def tick() -> None:
            if not self.busy:
                return
            color = PRIMARY if self._spinner_index % 2 == 0 else "#93b4f8"
            self.headline.set_text("●  正在检测硬件…", color)
            self._spinner_index += 1
            self._spinner_after = self.root.after(450, tick)

        tick()

    def _start_skeleton_wave(self) -> None:
        """骨架行自上而下循环高亮，形成扫描波。"""
        self._skeleton_index = 0

        def tick() -> None:
            if not self.busy:
                return
            rows = self.tree.get_children()
            for index, row in enumerate(rows):
                tag = "skel_hot" if index == self._skeleton_index else (
                    "skel_even" if index % 2 == 0 else "skel"
                )
                self.tree.item(row, tags=(tag,))
            self._skeleton_index = (self._skeleton_index + 1) % max(1, len(rows))
            self._skeleton_after = self.root.after(120, tick)

        self._skeleton_after = self.root.after(0, tick)

    def _start_progress(self) -> None:
        """确定式进度条，向目标值缓动，避免跳变。"""

        def tick() -> None:
            if not self.busy:
                return
            self._progress_value += (self._progress_target - self._progress_value) * 0.2
            self.progress.set(min(1.0, self._progress_value))
            self._progress_after = self.root.after(40, tick)

        tick()

    def _stop_loading(self) -> None:
        for attr in ("_spinner_after", "_skeleton_after", "_progress_after", "_status_after"):
            handle = getattr(self, attr)
            if handle:
                try:
                    self.root.after_cancel(handle)
                except Exception:
                    pass
                setattr(self, attr, None)
        self.progress.stop()
        self.progress.set(0)
        self.progress.pack_forget()

    # ---------- 轻量动效 ----------
    def _start_status_rotation(self) -> None:
        """检测期间轮播具体步骤，同时推进进度条目标值。"""
        self._status_index = 0
        self.status_var.set(STATUS_TEXTS[0])

        def tick() -> None:
            if not self.busy:
                return
            self._status_index = (self._status_index + 1) % len(STATUS_TEXTS)
            self.status_var.set(STATUS_TEXTS[self._status_index])
            # 最后一步留一点余量，真正完成时再跳到 100%
            self._progress_target = min(0.94, 0.12 + 0.22 * (self._status_index + 1))
            self._status_after = self.root.after(620, tick)

        self._status_after = self.root.after(620, tick)

    def _animate_score(self, target: int) -> None:
        """评分从 0 滚动到目标值，让结果出现得更有过程感。"""
        if self._score_after:
            try:
                self.root.after_cancel(self._score_after)
            except Exception:
                pass
            self._score_after = None
        self._score_value = 0
        step = max(1, target // 18)

        def tick() -> None:
            self._score_value = min(target, self._score_value + step)
            self.score_label.configure(text=f"综合评分：{self._score_value}/100")
            if self._score_value < target:
                self._score_after = self.root.after(20, tick)
            else:
                self._score_after = None

        tick()

    # ---------- 操作 ----------
    def copy_tree_selection(self, _event: tk.Event | None = None) -> None:
        """复制等级表中选中的行（制表符分隔，便于粘进表格软件）。"""
        rows = self.tree.selection()
        if not rows:
            self.status_var.set("请先点击选中表格行，再按 Ctrl+C 复制")
            return
        text = "\n".join("\t".join(self.tree.item(row, "values")) for row in rows)
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set(f"已复制表格 {len(rows)} 行")

    def copy_report(self) -> None:
        if not self.report:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.report)
        self.status_var.set("报告已复制到剪贴板")

    def save_report(self) -> None:
        if not self.report:
            return
        default = f"AI部署检测_{datetime.now():%Y%m%d_%H%M}.txt"
        path = filedialog.asksaveasfilename(
            title="保存检测报告（文本选 .txt，图片选 .png）",
            defaultextension=".txt",
            initialfile=default,
            filetypes=[("文本文件", "*.txt"), ("PNG 图片", "*.png")],
        )
        if not path:
            return
        suffix = os.path.splitext(path)[1].lower()
        if suffix == ".png":
            self._save_report_image(path)
            return
        if suffix not in ("", ".txt"):
            self.status_var.set("只支持保存为 .txt 或 .png")
            return
        try:
            # utf-8-sig 让记事本等工具正确识别中文
            with open(path, "w", encoding="utf-8-sig") as fh:
                fh.write(self.report)
        except OSError as exc:
            self.status_var.set(f"保存失败：{exc}")
            return
        self.status_var.set(f"报告已保存：{os.path.basename(path)}")

    def _save_report_image(self, path: str) -> None:
        """把整份报告拼成一张长图：逐屏滚动截图，再按滚动偏移拼接。"""
        canvas = self.body._parent_canvas
        box = canvas.bbox("all")
        if not box:
            self.status_var.set("保存图片失败：报告区域尚未渲染")
            return

        total = int(box[3])  # 内容包括的总高度（物理像素）
        view = canvas.winfo_height()
        left = canvas.winfo_rootx()
        top = canvas.winfo_rooty()
        width = canvas.winfo_width()
        if view <= 0 or width <= 0:
            self.status_var.set("保存图片失败：窗口尺寸异常")
            return

        self.status_var.set("正在生成报告图片…")
        self.root.update_idletasks()
        saved_scroll = canvas.yview()[0]
        saved_geometry = self.root.wm_geometry()
        moved = False
        rows: list[bytes | None] = [None] * total
        try:
            # 窗口有一部分在屏幕外时截不到内容，先把窗口挪进工作区
            if self._ensure_capture_visible(left, top, width, view):
                moved = True
                self.root.update_idletasks()
                self.root.update()
                left = canvas.winfo_rootx()
                top = canvas.winfo_rooty()

            pos = 0
            while pos < total:
                canvas.yview_moveto(pos / total)
                # 等 canvas 滚动与重绘完成，否则会截到上一屏的内容
                self.root.update_idletasks()
                self.root.update()
                time.sleep(0.12)
                self.root.update()

                rgb, win_w, win_h, win_left, win_top = grab_window_rgb(int(self.root.winfo_id()))
                stride = win_w * 3
                crop_x = left - win_left
                crop_y = top - win_top
                # canvasy(0) 是视口顶端对应的内容行号，取整可能差 1 px，用实测值定位
                start = int(round(canvas.canvasy(0)))
                for row in range(view):
                    dest = start + row
                    src_y = crop_y + row
                    if 0 <= dest < total and 0 <= src_y < win_h:
                        begin = src_y * stride + crop_x * 3
                        rows[dest] = rgb[begin : begin + width * 3]
                pos += view
        except OSError as exc:
            self.status_var.set(f"保存图片失败：{exc}")
            return
        finally:
            canvas.yview_moveto(saved_scroll)
            if moved:
                self.root.wm_geometry(saved_geometry)

        blank = bytes(width * 3)
        write_png(path, width, total, b"".join(row if row is not None else blank for row in rows))
        self.status_var.set(f"报告图片已保存：{os.path.basename(path)}（{width}×{total}）")

    def _ensure_capture_visible(self, left: int, top: int, width: int, height: int) -> bool:
        """待截区域超出工作区时把窗口平移回来，返回是否移动过。

        参数是待截区域的屏幕坐标（物理像素）；截图取的是屏幕上可见内容，
        区域在屏幕外会变成黑块，所以先移动窗口再截，截完由调用方复原。
        """
        wx, wy, ww, wh = work_area()
        if not ww or not wh:
            return False

        dx = dy = 0
        if left + width > wx + ww:
            dx = wx + ww - (left + width)
        if left + dx < wx:
            dx = wx - left
        if top + height > wy + wh:
            dy = wy + wh - (top + height)
        if top + dy < wy:
            dy = wy - top
        if dx == 0 and dy == 0:
            return False

        self.root.wm_geometry(
            f"{self.root.winfo_width()}x{self.root.winfo_height()}"
            f"+{self.root.winfo_x() + dx}+{self.root.winfo_y() + dy}"
        )
        return True

    def show_about(self) -> None:
        if self.hw is not None:
            # 去掉「（内部版本 …）」，避免对话框里折行
            os_name = self.hw.os_name.split("（")[0]
            arch = self.hw.arch
        else:
            machine = platform.machine()
            os_name = f"{platform.system()} {platform.release()}"
            arch = "64 位 (x64)" if machine.lower() in ("amd64", "x86_64") else machine
        runtime = "单文件 exe" if getattr(sys, "frozen", False) else "源码运行"
        messagebox.showinfo(
            "关于",
            f"{APP_TITLE}  v{VERSION}\n\n"
            "检测本机硬件，评估可在本地运行的开源大模型等级。\n"
            "不推荐具体模型，只说明这台电脑用 AI 能做到什么程度。\n\n"
            "检测内容\n"
            "  CPU 型号 / 核心 / 主频 / 当前占用\n"
            "  内存容量 / 频率 / 可用空间\n"
            "  显卡型号 / 显存 / 驱动版本\n"
            "  磁盘型号 / 可用空间\n"
            "  操作系统版本与架构\n\n"
            "评估方式\n"
            "  模型占用 ≈ 参数量 × 0.58 GB/B × 框架开销\n"
            "  按显存或内存余量判定：推荐 / 可用 / 紧张 / 不支持\n"
            "  分六档：入门级 ≤1.5B 至旗舰级 70B+，附速度与用途标签\n\n"
            "隐私说明\n"
            "  全部检测在本机完成，不联网、不上传任何数据。\n\n"
            "运行环境\n"
            f"  {os_name}\n"
            f"  {arch} · {system_dpi(self.root)} dpi · {runtime}",
            parent=self.root,
        )


def main() -> int:
    args = sys.argv[1:]
    if "--selftest" in args:
        idx = args.index("--selftest")
        target = ""
        if len(args) > idx + 1 and not args[idx + 1].startswith("-"):
            target = args[idx + 1]
        return run_selftest(target)
    if "--cli" in args:
        return run_cli()
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")
    root = ctk.CTk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
