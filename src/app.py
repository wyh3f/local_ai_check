"""本地 AI 部署能力检测工具。

用法：
    python app.py                启动图形界面
    python app.py --cli          在命令行直接输出检测报告
    python app.py --selftest [路径]  运行可移植性自检，结果写入 JSON
"""

from __future__ import annotations

import json
import os
import platform
import struct
import sys
import tempfile
import threading
import time
import tkinter as tk
from ctypes import wintypes
from datetime import datetime
from tkinter import ttk

from evaluator import Evaluation, evaluate
from hardware import HardwareInfo, detect_hardware

APP_TITLE = "本地 AI 部署能力检测"
VERSION = "1.0"
FONT_UI = ("Microsoft YaHei", 10)
FONT_TITLE = ("Microsoft YaHei", 18, "bold")
FONT_HEADLINE = ("Microsoft YaHei", 13, "bold")


def _work_area() -> tuple[int, int, int, int]:
    """读取不含任务栏的桌面工作区，返回 (x, y, 宽, 高)，失败时返回全零。"""
    try:
        import ctypes

        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    except Exception:
        pass
    return 0, 0, 0, 0


def build_report(hw: HardwareInfo, ev: Evaluation) -> str:
    """把检测结果拼装成纯文本报告，供复制或命令行输出。"""
    gpu_lines = (
        "\n".join(f"                {g.name}（{g.detail}）" for g in hw.gpus)
        or "                未检测到显卡"
    )
    ram_line = f"{hw.ram_total_gb:g} GB"
    if hw.ram_speed_mhz or hw.ram_modules:
        ram_line += f"（{hw.ram_speed_mhz} MHz，{hw.ram_modules} 条）"

    lines = [
        "===== 本地 AI 部署能力检测报告 =====",
        f"程序版本：v{VERSION}",
        f"检测时间：{datetime.now():%Y-%m-%d %H:%M:%S}",
        "",
        "[系统信息]",
        f"  操作系统 ：{hw.os_name}",
        f"  系统架构 ：{hw.arch}",
        f"  计算机名 ：{hw.hostname}",
        f"  当前用户 ：{hw.username}",
        f"  内存使用 ：{hw.ram_percent}%（可用 {hw.ram_available_gb:g} GB / 共 {hw.ram_total_gb:g} GB）",
        "",
        "[硬件信息]",
        f"  CPU      ：{hw.cpu_name}",
        f"               {hw.cpu_cores} 核 {hw.cpu_threads} 线程 @ {hw.cpu_freq_mhz} MHz",
        f"  内存     ：{ram_line}",
        f"  显卡     ：{gpu_lines.strip()}",
        f"  磁盘     ：{hw.disk_free_gb:g} GB 可用 / 共 {hw.disk_total_gb:g} GB",
        "",
        "[评估结论]",
        f"  是否适合部署：{'是' if ev.deployable else '否'}",
        f"  推荐推理路径：{ev.path}",
        f"  资源池      ：{ev.pool_label} {ev.pool_gb:g} GB（推荐线 {ev.rec_line:g}%，可用线 {ev.ok_line:g}%）",
        f"  最高承受等级：{ev.tier}{'（' + ev.best.params_desc + ' 参数）' if ev.best else ''}",
        f"  用途适配    ：" + "  ".join(f"{name} {mark}" for name, mark in ev.usage_tags),
        f"  综合评分    ：{ev.score}/100",
        f"  结论        ：{ev.headline}",
    ]
    if ev.tier_desc:
        lines.append(f"  等级说明    ：{ev.tier_desc}")

    lines += [
        "",
        "[等级对照（按 4-bit 量化估算）]",
        "  等级     参数量级    4-bit 占用   结论    判定依据                预估速度                适用场景",
    ]
    for t in ev.tiers:
        if t.speed == "不可用":
            speed_text = "跑不动"
        else:
            speed_text = f"{t.speed} tok/s（{t.speed_level}）"
        lines.append(
            f"  {t.label:<7} {t.params_desc:<10} {t.need_gb:>6.1f} GB   {t.verdict:<5} "
            f"{t.reason:<22} {speed_text:<24} {t.scene}"
        )

    lines += ["", "[使用 AI 能做到什么程度]"]
    for idx, tip in enumerate(ev.advice, 1):
        lines.append(f"  {idx}. {tip}")
    return "\n".join(lines)


def run_cli() -> int:
    hw = detect_hardware()
    ev = evaluate(hw)
    print(build_report(hw, ev))
    return 0


class App:
    """图形界面主窗口。"""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.report = ""
        self.busy = False
        root.title(f"{APP_TITLE} v{VERSION}")
        # 按系统工作区自适应，避免任务栏遮挡或小屏笔记本内容被裁掉
        wx, wy, ww, wh = _work_area()
        if not ww or not wh:
            wx, wy, ww, wh = 0, 0, root.winfo_screenwidth(), root.winfo_screenheight()
        width = min(960, max(760, ww - 120))
        # geometry 不含标题栏，额外预留约 40px，保证整窗落在工作区内
        height = min(820, max(480, wh - 120))
        x = wx + max(0, (ww - width) // 2)
        y = wy + max(0, (wh - height) // 2)
        root.geometry(f"{width}x{height}+{x}+{y}")
        root.minsize(720, 480)

        self._init_style()
        self._build_layout()
        self.refresh()

    # ---------- 界面搭建 ----------
    def _init_style(self) -> None:
        style = ttk.Style()
        style.theme_use("clam")
        style.configure(".", font=FONT_UI)
        style.configure("Card.TFrame", background="#ffffff", relief="solid", borderwidth=1)
        style.configure("Plain.TFrame", background="#ffffff", borderwidth=0)
        style.configure("Headline.TLabel", font=FONT_HEADLINE, background="#ffffff")
        style.configure("Muted.TLabel", foreground="#5f6368", background="#ffffff")
        style.configure("Treeview", rowheight=26, font=FONT_UI)
        style.configure("Treeview.Heading", font=("Microsoft YaHei", 10, "bold"))
        style.configure("Accent.TButton", font=("Microsoft YaHei", 10, "bold"))

    def _build_layout(self) -> None:
        # 底部操作栏固定在窗口底部，不随内容滚动
        bar = ttk.Frame(self.root, padding=(16, 10))
        bar.pack(side="bottom", fill="x")
        self.refresh_btn = ttk.Button(bar, text="重新检测", style="Accent.TButton", command=self.refresh)
        self.refresh_btn.pack(side="left")
        ttk.Button(bar, text="复制报告", command=self.copy_report).pack(side="left", padx=8)
        self.status_var = tk.StringVar(value="")
        ttk.Label(bar, textvariable=self.status_var, foreground="#5f6368").pack(side="right")

        # 用 Canvas 承载内容，窗口较小时可以滚动查看完整信息
        container = ttk.Frame(self.root)
        container.pack(fill="both", expand=True)
        bg = ttk.Style().lookup("TFrame", "background")
        canvas = tk.Canvas(container, highlightthickness=0, background=bg)
        scrollbar = ttk.Scrollbar(container, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side="right", fill="y")
        canvas.pack(side="left", fill="both", expand=True)

        outer = ttk.Frame(canvas, padding=16)
        window_id = canvas.create_window((0, 0), window=outer, anchor="nw")
        outer.bind("<Configure>", lambda _e: canvas.configure(scrollregion=canvas.bbox("all")))
        canvas.bind("<Configure>", lambda e: canvas.itemconfigure(window_id, width=e.width))

        def on_wheel(event: tk.Event) -> None:
            if isinstance(event.widget, tk.Text):
                return
            canvas.yview_scroll(int(-event.delta / 120), "units")

        canvas.bind_all("<MouseWheel>", on_wheel)

        ttk.Label(outer, text=APP_TITLE, font=FONT_TITLE).pack(anchor="w")
        ttk.Label(
            outer,
            text="检测本机 CPU、内存、显卡与磁盘，评估能否在本地运行开源大模型",
            foreground="#5f6368",
        ).pack(anchor="w", pady=(2, 12))

        # 结论卡片
        card = ttk.Frame(outer, style="Card.TFrame", padding=14)
        card.pack(fill="x")
        self.headline_var = tk.StringVar(value="正在检测…")
        self.headline_label = ttk.Label(card, textvariable=self.headline_var, style="Headline.TLabel")
        self.headline_label.pack(anchor="w")

        meta = ttk.Frame(card, style="Plain.TFrame")
        meta.pack(fill="x", pady=(8, 0))
        self.tier_var = tk.StringVar(value="等级：—")
        self.path_var = tk.StringVar(value="推理路径：—")
        self.budget_var = tk.StringVar(value="预算：—")
        self.score_var = tk.StringVar(value="评分：—")
        for var in (self.tier_var, self.path_var, self.budget_var, self.score_var):
            ttk.Label(meta, textvariable=var, style="Muted.TLabel").pack(side="left", padx=(0, 22))

        self.tags_var = tk.StringVar(value="")
        ttk.Label(card, textvariable=self.tags_var, style="Muted.TLabel").pack(anchor="w", pady=(6, 0))

        # 系统信息
        sys_box = ttk.LabelFrame(outer, text=" 系统信息 ", padding=10)
        sys_box.pack(fill="x", pady=(14, 0))
        self.sys_vars: dict[str, tk.StringVar] = {}
        for row, (key, label) in enumerate(
            [
                ("os", "操作系统"),
                ("arch", "系统架构"),
                ("host", "计算机名"),
                ("user", "当前用户"),
                ("mem", "内存使用"),
            ]
        ):
            ttk.Label(sys_box, text=label, width=9).grid(row=row, column=0, sticky="w", pady=2)
            var = tk.StringVar(value="—")
            self.sys_vars[key] = var
            ttk.Label(sys_box, textvariable=var, wraplength=760, justify="left").grid(
                row=row, column=1, sticky="w", pady=2
            )
        sys_box.columnconfigure(1, weight=1)

        # 硬件信息
        hw_box = ttk.LabelFrame(outer, text=" 硬件信息 ", padding=10)
        hw_box.pack(fill="x", pady=(10, 0))
        self.hw_vars: dict[str, tk.StringVar] = {}
        for row, (key, label) in enumerate(
            [("cpu", "CPU"), ("gpu", "显卡"), ("ram", "内存"), ("disk", "磁盘")]
        ):
            ttk.Label(hw_box, text=label, width=8).grid(row=row, column=0, sticky="nw", pady=2)
            var = tk.StringVar(value="—")
            self.hw_vars[key] = var
            ttk.Label(hw_box, textvariable=var, wraplength=760, justify="left").grid(
                row=row, column=1, sticky="w", pady=2
            )
        hw_box.columnconfigure(1, weight=1)

        # 等级对照表
        model_box = ttk.LabelFrame(outer, text=" 等级对照（按 4-bit 量化估算） ", padding=8)
        model_box.pack(fill="x", pady=(14, 0))
        columns = ("tier", "params", "need", "verdict", "reason", "speed", "scene")
        self.tree = ttk.Treeview(model_box, columns=columns, show="headings", height=6)
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
            self.tree.column(key, width=width, anchor="w" if key == "scene" else "center")
        self.tree.tag_configure("ok", foreground="#1b5e20")
        self.tree.tag_configure("usable", foreground="#f9a825")
        self.tree.tag_configure("tight", foreground="#e65100")
        self.tree.tag_configure("no", foreground="#9e9e9e")
        self.tree.pack(fill="x")

        # 能力说明
        advice_box = ttk.LabelFrame(outer, text=" 这台电脑用 AI 能做什么 ", padding=8)
        advice_box.pack(fill="x", pady=(14, 0))
        self.advice_text = tk.Text(advice_box, height=9, wrap="word", font=FONT_UI, relief="flat")
        self.advice_text.pack(fill="x")
        self.advice_text.configure(state="disabled")

    # ---------- 检测流程 ----------
    def refresh(self) -> None:
        if self.busy:
            return
        self.busy = True
        self.refresh_btn.configure(state="disabled")
        self.status_var.set("正在检测硬件…")
        self.headline_var.set("正在检测…")
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
        self.refresh_btn.configure(state="normal")
        if error or hw is None or ev is None:
            self.headline_var.set("检测失败")
            self.status_var.set(f"错误：{error}")
            return

        self.report = build_report(hw, ev)
        self.headline_var.set(ev.headline)
        if not ev.deployable:
            color = "#c62828"
        elif any(t.verdict == "推荐" for t in ev.tiers):
            color = "#1b5e20" if ev.score >= 45 else "#e65100"
        else:
            color = "#e65100"
        self.headline_label.configure(foreground=color)

        self.tier_var.set(f"等级：{ev.tier}")
        self.path_var.set(f"推理路径：{ev.path}")
        self.budget_var.set(
            f"资源池：{ev.pool_label} {ev.pool_gb:g} GB（推荐线 {ev.rec_line:g}%）"
        )
        self.score_var.set(f"综合评分：{ev.score}/100")
        self.tags_var.set("用途：" + "   ".join(f"{name} {mark}" for name, mark in ev.usage_tags))

        self.sys_vars["os"].set(hw.os_name)
        self.sys_vars["arch"].set(hw.arch)
        self.sys_vars["host"].set(hw.hostname)
        self.sys_vars["user"].set(hw.username)
        self.sys_vars["mem"].set(
            f"{hw.ram_percent}%（可用 {hw.ram_available_gb:g} GB / 共 {hw.ram_total_gb:g} GB）"
        )

        self.hw_vars["cpu"].set(
            f"{hw.cpu_name}\n{hw.cpu_cores} 核 {hw.cpu_threads} 线程 @ {hw.cpu_freq_mhz} MHz"
        )
        gpu_text = "\n".join(f"{g.name}（{g.detail}）" for g in hw.gpus) or "未检测到显卡"
        self.hw_vars["gpu"].set(gpu_text)
        ram_text = f"{hw.ram_total_gb:g} GB（可用 {hw.ram_available_gb:g} GB"
        if hw.ram_speed_mhz:
            ram_text += f"，{hw.ram_speed_mhz} MHz"
        self.hw_vars["ram"].set(ram_text + "）")
        self.hw_vars["disk"].set(f"{hw.disk_free_gb:g} GB 可用 / 共 {hw.disk_total_gb:g} GB")

        self.tree.delete(*self.tree.get_children())
        for t in ev.tiers:
            tag = {"推荐": "ok", "可用": "usable", "勉强": "tight"}.get(t.verdict, "no")
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
                tags=(tag,),
            )

        self.advice_text.configure(state="normal")
        self.advice_text.delete("1.0", "end")
        for idx, tip in enumerate(ev.advice, 1):
            self.advice_text.insert("end", f"{idx}. {tip}\n")
        self.advice_text.configure(state="disabled")

        self.status_var.set(f"检测完成，耗时 {elapsed:.1f} 秒")

    def copy_report(self) -> None:
        if not self.report:
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.report)
        self.status_var.set("报告已复制到剪贴板")


def run_selftest(output: str = "") -> int:
    """可移植性自检：逐项验证运行环境，结果写入 JSON 文件。

    返回 0 表示全部关键项通过，1 表示有失败项，2 表示结果文件写不出去。
    """
    checks: list[dict[str, object]] = []

    def check(name: str, func, optional: bool = False) -> None:
        try:
            ok, detail = func()
        except Exception as exc:  # 自检要记录失败，而不是直接崩溃
            ok, detail = False, f"{type(exc).__name__}: {exc}"
        checks.append({"name": name, "ok": bool(ok), "detail": str(detail), "optional": optional})

    def frozen_probe() -> tuple[bool, str]:
        base = getattr(sys, "_MEIPASS", "")
        return bool(getattr(sys, "frozen", False)), f"_MEIPASS={base or '无（源码运行）'}"

    def temp_probe() -> tuple[bool, str]:
        with tempfile.NamedTemporaryFile(prefix="cs_ai_check_", delete=True) as fh:
            fh.write(b"ok")
        return True, tempfile.gettempdir()

    def tk_probe() -> tuple[bool, str]:
        root = tk.Tk()
        root.withdraw()
        patch = root.tk.call("info", "patchlevel")
        root.update_idletasks()
        root.destroy()
        return True, f"Tcl/Tk {patch}"

    def hw_probe() -> tuple[bool, str]:
        hw = detect_hardware()
        ok = bool(hw.cpu_name) and hw.ram_total_gb > 0
        gpus = "、".join(
            f"{g.name}（{g.vram_gb:g} GB）" if g.vram_gb else f"{g.name}（核显）" for g in hw.gpus
        ) or "未检测到"
        return ok, f"CPU={hw.cpu_name}; 内存={hw.ram_total_gb:g} GB; 显卡={gpus}"

    def eval_probe() -> tuple[bool, str]:
        ev = evaluate(detect_hardware())
        return bool(ev.tiers), f"等级={ev.tier}; 评分={ev.score}; 等级行数={len(ev.tiers)}"

    def report_probe() -> tuple[bool, str]:
        hw = detect_hardware()
        text = build_report(hw, evaluate(hw))
        return len(text) > 500, f"报告 {len(text)} 字符"

    def font_probe() -> tuple[bool, str]:
        import tkinter.font as tkfont

        root = tk.Tk()
        root.withdraw()
        families = set(tkfont.families(root))
        root.destroy()
        # 中文系统里 Tk 返回的是本地化名称，需同时匹配英文名和「微软雅黑」
        candidates = {"Microsoft YaHei", "Microsoft YaHei UI", "微软雅黑"}
        available = bool(candidates & families)
        return available, "微软雅黑可用" if available else "微软雅黑缺失，中文会回退到默认字体"

    check("单文件运行环境", frozen_probe)
    check("临时目录可写", temp_probe)
    check("Tk 图形栈", tk_probe)
    check("硬件采集", hw_probe)
    check("等级评估", eval_probe)
    check("报告生成", report_probe)
    check("中文字体", font_probe, optional=True)

    passed = all(c["ok"] for c in checks if not c["optional"])
    result = {
        "app": APP_TITLE,
        "version": VERSION,
        "time": datetime.now().isoformat(timespec="seconds"),
        "passed": passed,
        "python": platform.python_version(),
        "bits": struct.calcsize("P") * 8,
        "os": platform.platform(),
        "checks": checks,
    }
    target = output or os.path.join(tempfile.gettempdir(), "cs_ai_check_selftest.json")
    try:
        folder = os.path.dirname(os.path.abspath(target))
        if folder:
            os.makedirs(folder, exist_ok=True)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False, indent=2)
    except Exception:
        return 2
    return 0 if passed else 1


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
    root = tk.Tk()
    App(root)
    root.mainloop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
