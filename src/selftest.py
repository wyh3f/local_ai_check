"""可移植性自检：逐项验证运行环境，结果写入 JSON。"""

from __future__ import annotations

import json
import os
import platform
import struct
import sys
import tempfile
import tkinter as tk
from datetime import datetime

from appinfo import APP_TITLE, VERSION
from evaluator import evaluate
from hardware import detect_hardware
from platform_win import asset_path, enable_dpi_awareness
from report import build_report


def run_selftest(output: str = "") -> int:
    """逐项验证运行环境，返回 0 全部关键项通过，1 有失败项，2 结果写不出去。"""
    enable_dpi_awareness()
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

    def ctk_probe() -> tuple[bool, str]:
        import customtkinter as ctk

        root = ctk.CTk()
        root.withdraw()
        root.update_idletasks()
        root.destroy()
        return True, f"customtkinter {ctk.__version__}"

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

    def icon_probe() -> tuple[bool, str]:
        path = asset_path("icon.ico")
        return os.path.isfile(path), f"{path}（{os.path.getsize(path) // 1024} KB）"

    def dpi_probe() -> tuple[bool, str]:
        if sys.platform != "win32":
            return True, "非 Windows，跳过"
        import ctypes

        dpi = int(ctypes.windll.user32.GetDpiForSystem())
        return True, f"{dpi} dpi（{'已感知' if dpi >= 108 else '未感知，界面会被拉伸'}）"

    check("单文件运行环境", frozen_probe)
    check("临时目录可写", temp_probe)
    check("Tk 图形栈", tk_probe)
    check("界面组件库", ctk_probe)
    check("硬件采集", hw_probe)
    check("等级评估", eval_probe)
    check("报告生成", report_probe)
    check("图标资源", icon_probe)
    check("DPI 感知", dpi_probe)
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
