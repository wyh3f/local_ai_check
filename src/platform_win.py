"""平台相关工具：资源定位、桌面工作区、DPI 感知与缩放。

与界面无关，可被界面、自检等模块直接复用。
"""

from __future__ import annotations

import os
import sys
import tkinter as tk
from ctypes import wintypes


def asset_path(name: str) -> str:
    """定位随程序分发的资源，兼容源码运行与单文件打包两种模式。"""
    base = getattr(sys, "_MEIPASS", os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "assets", name)


def work_area() -> tuple[int, int, int, int]:
    """读取不含任务栏的桌面工作区，返回 (x, y, 宽, 高)，失败时返回全零。"""
    try:
        import ctypes

        rect = wintypes.RECT()
        if ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):
            return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top
    except Exception:
        pass
    return 0, 0, 0, 0


def enable_dpi_awareness() -> None:
    """开启 DPI 感知，避免 125%/150% 缩放下整个界面被位图拉伸发虚。"""
    if sys.platform != "win32":
        return
    import ctypes

    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(1)  # Win8.1+
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()  # Win7 兜底
        except Exception:
            pass


def system_dpi(root: tk.Tk) -> int:
    """读取真实屏幕 DPI。per-monitor 感知下 Tk 自报 96，需直接问系统。"""
    if sys.platform == "win32":
        import ctypes

        try:
            dpi = int(ctypes.windll.user32.GetDpiForSystem())
            if 60 <= dpi <= 400:
                return dpi
        except Exception:
            pass
    try:
        return int(root.winfo_fpixels("1i"))
    except Exception:
        return 96
