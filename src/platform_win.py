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


def _encode_png(width: int, height: int, rgb: bytes) -> bytes:
    """把自上而下的 RGB 字节流编码成 PNG（zlib 压缩，不依赖 Pillow）。"""
    import struct
    import zlib

    stride = width * 3
    raw = bytearray((stride + 1) * height)
    for y in range(height):
        dst = y * (stride + 1)
        raw[dst] = 0  # 每行过滤器类型 0（None）
        raw[dst + 1 : dst + 1 + stride] = rgb[y * stride : (y + 1) * stride]

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(bytes(raw), 6))
        + chunk(b"IEND", b"")
    )


def _dib_rgb(memory_dc: int, bitmap: int, width: int, height: int) -> bytes:
    """从已选入位图的内存 DC 读像素，返回自上而下的 RGB 字节流。"""
    import ctypes

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD),
            ("biWidth", wintypes.LONG),
            ("biHeight", wintypes.LONG),
            ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD),
            ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD),
            ("biXPelsPerMeter", wintypes.LONG),
            ("biYPelsPerMeter", wintypes.LONG),
            ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    info = BITMAPINFOHEADER()
    info.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    info.biWidth = width
    info.biHeight = -height  # 负高度表示自上而下
    info.biPlanes = 1
    info.biBitCount = 32
    info.biCompression = 0  # BI_RGB

    buffer = ctypes.create_string_buffer(width * height * 4)
    if not ctypes.windll.gdi32.GetDIBits(memory_dc, bitmap, 0, height, buffer, ctypes.byref(info), 0):
        raise OSError("读取像素失败")

    # BGRA → RGB，用切片赋值避免逐像素循环
    src = buffer.raw
    rgb = bytearray(width * height * 3)
    rgb[0::3] = src[2::4]
    rgb[1::3] = src[1::4]
    rgb[2::3] = src[0::4]
    return bytes(rgb)


def grab_window_rgb(hwnd: int) -> tuple[bytes, int, int, int, int]:
    """渲染整个窗口，返回 (rgb, 宽, 高, 窗口 left, 窗口 top)。

    用 PrintWindow(PW_RENDERFULLCONTENT) 渲染窗口自身，画面里不会混入遮挡它的
    其它窗口或系统水印；失败时退回 BitBlt 窗口可见区域。
    """
    if sys.platform != "win32":
        raise OSError("仅支持 Windows")

    import ctypes

    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32

    target = user32.GetAncestor(hwnd, 2) or hwnd  # GA_ROOT：带标题栏的顶层窗口
    rect = wintypes.RECT()
    if not user32.GetWindowRect(target, ctypes.byref(rect)):
        raise OSError("读取窗口尺寸失败")
    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise OSError("窗口尺寸无效")

    window_dc = user32.GetWindowDC(target)
    memory_dc = gdi32.CreateCompatibleDC(window_dc)
    bitmap = gdi32.CreateCompatibleBitmap(window_dc, width, height)
    gdi32.SelectObject(memory_dc, bitmap)
    try:
        if not user32.PrintWindow(target, memory_dc, 2):
            gdi32.BitBlt(memory_dc, 0, 0, width, height, window_dc, 0, 0, 0x00CC0020)
        rgb = _dib_rgb(memory_dc, bitmap, width, height)
    finally:
        gdi32.DeleteObject(bitmap)
        gdi32.DeleteDC(memory_dc)
        user32.ReleaseDC(target, window_dc)

    return rgb, width, height, rect.left, rect.top


def write_png(path: str, width: int, height: int, rgb: bytes) -> None:
    """把 RGB 字节流写成 PNG 文件。"""
    with open(path, "wb") as fh:
        fh.write(_encode_png(width, height, rgb))


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
