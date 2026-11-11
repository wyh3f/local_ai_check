"""硬件信息采集模块，仅依赖 Python 标准库。

数据来源优先级：
1. nvidia-smi（NVIDIA 独显，显存最准确）
2. PowerShell + Get-CimInstance（CPU、内存条、通用显卡信息）
3. ctypes 的 GlobalMemoryStatusEx（内存容量，最快且不受 WMI 影响）
"""

from __future__ import annotations

import ctypes
import getpass
import json
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass, field

CREATE_NO_WINDOW = 0x08000000

# 核显/集成显卡的典型名称关键字
INTEGRATED_KEYWORDS = (
    "uhd graphics",
    "hd graphics",
    "iris",
    "radeon graphics",
    "radeon(tm) graphics",
    "vega",
    "apple m",
    "microsoft basic display",
)


@dataclass
class GpuInfo:
    """单块显卡信息。"""

    name: str
    vram_gb: float  # 显存容量（GB），0 表示未知或共享
    kind: str  # nvidia / discrete / integrated
    detail: str = ""
    driver: str = ""


@dataclass
class HardwareInfo:
    """整机硬件信息汇总。"""

    cpu_name: str = "未知"
    cpu_cores: int = 0
    cpu_threads: int = 0
    cpu_freq_mhz: int = 0
    ram_total_gb: float = 0.0
    ram_available_gb: float = 0.0
    ram_speed_mhz: int = 0
    ram_modules: int = 0
    gpus: list[GpuInfo] = field(default_factory=list)
    disk_total_gb: float = 0.0
    disk_free_gb: float = 0.0
    os_name: str = ""
    hostname: str = ""
    username: str = ""
    arch: str = ""
    ram_percent: int = 0
    cpu_usage: int = -1  # 当前 CPU 占用率（%），-1 表示未采样
    disk_model: str = ""

    @property
    def dgpu(self) -> GpuInfo | None:
        """返回最适合跑推理的独立显卡，没有则返回 None。"""
        candidates = [g for g in self.gpus if g.kind in ("nvidia", "discrete")]
        if not candidates:
            return None
        return max(candidates, key=lambda g: g.vram_gb)


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", wintypes.DWORD),
        ("dwMemoryLoad", wintypes.DWORD),
        ("ullTotalPhys", ctypes.c_ulonglong),
        ("ullAvailPhys", ctypes.c_ulonglong),
        ("ullTotalPageFile", ctypes.c_ulonglong),
        ("ullAvailPageFile", ctypes.c_ulonglong),
        ("ullTotalVirtual", ctypes.c_ulonglong),
        ("ullAvailVirtual", ctypes.c_ulonglong),
        ("ullAvailExtendedVirtual", ctypes.c_ulonglong),
    ]


def _read_memory() -> tuple[float, float, int]:
    """用 GlobalMemoryStatusEx 读取物理内存总量、可用量与使用率（GB / %）。"""
    if sys.platform != "win32":
        return 0.0, 0.0, 0
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return 0.0, 0.0, 0
    gb = 1024 ** 3
    return status.ullTotalPhys / gb, status.ullAvailPhys / gb, int(status.dwMemoryLoad)


def _read_cpu_usage(sample_seconds: float = 0.3) -> int:
    """两次采样 GetSystemTimes，算出当前 CPU 占用率（%）。"""
    if sys.platform != "win32":
        return -1

    class _FileTime(ctypes.Structure):
        _fields_ = [("low", wintypes.DWORD), ("high", wintypes.DWORD)]

    def snapshot() -> tuple[int, int, int] | None:
        idle, kernel, user = _FileTime(), _FileTime(), _FileTime()
        ok = ctypes.windll.kernel32.GetSystemTimes(
            ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
        )
        if not ok:
            return None
        to_int = lambda ft: (ft.high << 32) + ft.low  # noqa: E731
        return to_int(idle), to_int(kernel), to_int(user)

    try:
        first = snapshot()
        time.sleep(sample_seconds)
        second = snapshot()
        if not first or not second:
            return -1
        idle = second[0] - first[0]
        total = (second[1] - first[1]) + (second[2] - first[2])
        if total <= 0:
            return -1
        return max(0, min(100, round(100 * (1 - idle / total))))
    except Exception:
        return -1


def _localize_product(product: str) -> str:
    """把 Windows 英文产品名换成中文版本名。"""
    editions = [
        ("Pro for Workstations", "专业工作站版"),
        ("Pro", "专业版"),
        ("Enterprise", "企业版"),
        ("Education", "教育版"),
        ("Home", "家庭版"),
    ]
    for english, chinese in editions:
        if english.lower() in product.lower():
            base = "Windows 11" if "11" in product else "Windows 10"
            return f"{base} {chinese}"
    return product


def _read_os_name() -> str:
    """读取 Windows 产品名、版本号与内部版本号。"""
    try:
        import winreg

        key_path = r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            product = str(winreg.QueryValueEx(key, "ProductName")[0])
            build = str(winreg.QueryValueEx(key, "CurrentBuild")[0])
            try:
                display = str(winreg.QueryValueEx(key, "DisplayVersion")[0])
            except OSError:
                display = ""
            try:
                ubr = str(winreg.QueryValueEx(key, "UBR")[0])
            except OSError:
                ubr = ""
        # Windows 11 的 ProductName 仍写着 Windows 10，用内部版本号区分
        if build.isdigit() and int(build) >= 22000:
            product = product.replace("Windows 10", "Windows 11")
        product = _localize_product(product)
        parts = [product, display]
        name = " ".join(p for p in parts if p)
        if build:
            name += f"（内部版本 {build}.{ubr}）" if ubr else f"（内部版本 {build}）"
        return name
    except Exception:
        return f"{platform.system()} {platform.release()}"


def _read_arch() -> str:
    machine = platform.machine().lower()
    if machine in ("amd64", "x86_64"):
        return "64 位 (x64)"
    if machine in ("arm64", "aarch64"):
        return "64 位 (ARM64)"
    if machine in ("x86", "i386", "i686"):
        return "32 位 (x86)"
    return machine or "未知"


def _run(cmd: list[str], timeout: int = 20) -> str:
    """执行外部命令并返回 stdout，失败时返回空串。"""
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    except (OSError, subprocess.SubprocessError):
        return ""
    if proc.returncode != 0:
        return ""
    return proc.stdout.decode("utf-8", errors="replace").strip()


_CIM_SCRIPT = r"""
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$cpu = Get-CimInstance Win32_Processor | Select-Object -First 1
$cs  = Get-CimInstance Win32_ComputerSystem
$gpus = @(Get-CimInstance Win32_VideoController)
$mem = @(Get-CimInstance Win32_PhysicalMemory)
$result = [ordered]@{
    os = [System.Environment]::OSVersion.VersionString
    cpu = @{
        name = $cpu.Name
        cores = $cpu.NumberOfCores
        threads = $cpu.NumberOfLogicalProcessors
        freq = $cpu.MaxClockSpeed
    }
    gpus = @($gpus | ForEach-Object {
        @{ name = $_.Name; vram = [double]$_.AdapterRAM; driver = $_.DriverVersion }
    })
    disk = (Get-CimInstance Win32_DiskDrive | Select-Object -First 1).Model
    ram = @{
        total = [double]$cs.TotalPhysicalMemory
        modules = @($mem | ForEach-Object { @{ capacity = [double]$_.Capacity; speed = $_.Speed } })
    }
}
$result | ConvertTo-Json -Depth 5 -Compress
"""


def _read_cim() -> dict:
    raw = _run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _CIM_SCRIPT])
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {}


def _read_nvidia() -> list[GpuInfo]:
    """通过 nvidia-smi 读取 NVIDIA 显卡型号与显存。"""
    raw = _run(
        [
            "nvidia-smi",
            "--query-gpu=name,memory.total",
            "--format=csv,noheader,nounits",
        ],
        timeout=8,
    )
    if not raw:
        return []
    gpus: list[GpuInfo] = []
    for line in raw.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) < 2 or not parts[0]:
            continue
        try:
            vram_gb = float(parts[1]) / 1024
        except ValueError:
            continue
        gpus.append(
            GpuInfo(name=parts[0], vram_gb=round(vram_gb, 1), kind="nvidia", detail="nvidia-smi")
        )
    return gpus


_GPU_CLASS_KEY = r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}"


def _read_registry_gpus() -> dict[str, float]:
    """从注册表读取显卡型号与显存（GB）。

    WMI 的 AdapterRAM 只有 32 位，超过 4GB 会溢出，AMD/Intel 独显必须靠这里补。
    """
    try:
        import winreg
    except ImportError:
        return {}
    try:
        root = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, _GPU_CLASS_KEY)
    except OSError:
        return {}

    found: dict[str, float] = {}
    try:
        for index in range(64):
            try:
                sub = winreg.EnumKey(root, index)
            except OSError:
                break
            if not sub.isdigit():
                continue
            try:
                with winreg.OpenKey(root, sub) as key:
                    desc = str(winreg.QueryValueEx(key, "DriverDesc")[0]).strip()
                    size = 0.0
                    # NVIDIA 用 qwMemorySize，部分 AMD/Intel 驱动只写 DedicatedVideoMemory
                    for value_name in (
                        "HardwareInformation.qwMemorySize",
                        "HardwareInformation.DedicatedVideoMemory",
                    ):
                        try:
                            size = float(winreg.QueryValueEx(key, value_name)[0])
                        except (OSError, ValueError):
                            continue
                        if size > 0:
                            break
            except (OSError, ValueError):
                continue
            if desc and size > 0:
                found[desc] = round(size / 1024 ** 3, 1)
    finally:
        root.Close()
    return found


def _read_cpu_from_registry() -> str:
    """CIM 不可用时，从注册表兜底读取 CPU 名称。"""
    try:
        import winreg

        path = r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, path) as key:
            return " ".join(str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).split())
    except Exception:
        return ""


def _match_vram(name: str, registry: dict[str, float]) -> float:
    """把注册表显存匹配到 CIM 返回的显卡名上。"""
    if name in registry:
        return registry[name]
    lowered = name.lower()
    for desc, size in registry.items():
        other = desc.lower()
        if lowered in other or other in lowered:
            return size
    if len(registry) == 1:
        return next(iter(registry.values()))
    return 0.0


def _classify_gpu(name: str, vram_bytes: float) -> tuple[str, float]:
    """判断显卡类型并换算显存，返回 (类型, 显存GB)。"""
    lowered = name.lower()
    if any(key in lowered for key in INTEGRATED_KEYWORDS):
        return "integrated", 0.0

    vram_gb = vram_bytes / 1024 ** 3 if vram_bytes > 0 else 0.0
    # CIM 的 AdapterRAM 是 32 位字段，超过 4GB 会溢出，这类值不可信
    if vram_bytes >= 4.29e9:
        vram_gb = 0.0
    if "nvidia" in lowered:
        return "nvidia", round(vram_gb, 1)
    return "discrete", round(vram_gb, 1)


def _read_disk() -> tuple[float, float]:
    root = os.environ.get("SystemDrive", "C:") + "\\"
    try:
        usage = shutil.disk_usage(root)
    except OSError:
        return 0.0, 0.0
    gb = 1024 ** 3
    return usage.total / gb, usage.free / gb


def detect_hardware() -> HardwareInfo:
    """采集整机硬件信息，任何单项失败都不影响其余项目。"""
    info = HardwareInfo()

    total, available, percent = _read_memory()
    info.ram_total_gb = round(total, 1)
    info.ram_available_gb = round(available, 1)
    info.ram_percent = percent
    info.os_name = _read_os_name()
    info.hostname = socket.gethostname()
    try:
        info.username = getpass.getuser()
    except Exception:
        info.username = os.environ.get("USERNAME", "")
    info.arch = _read_arch()

    cim = _read_cim()
    if cim:
        cpu = cim.get("cpu") or {}
        if cpu.get("name"):
            info.cpu_name = " ".join(str(cpu["name"]).split())
        info.cpu_cores = int(cpu.get("cores") or 0)
        info.cpu_threads = int(cpu.get("threads") or 0)
        info.cpu_freq_mhz = int(cpu.get("freq") or 0)

        ram = cim.get("ram") or {}
        modules = ram.get("modules") or []
        if not isinstance(modules, list):
            modules = [modules]
        info.ram_modules = len(modules)
        speeds = [m.get("speed") or 0 for m in modules if isinstance(m, dict)]
        info.ram_speed_mhz = max(speeds) if speeds else 0
        if not info.ram_total_gb and ram.get("total"):
            info.ram_total_gb = round(float(ram["total"]) / 1024 ** 3, 1)

        if cim.get("disk"):
            info.disk_model = " ".join(str(cim["disk"]).split())

        gpus = cim.get("gpus") or []
        if not isinstance(gpus, list):
            gpus = [gpus]
        registry_vram = _read_registry_gpus()
        for item in gpus:
            if not isinstance(item, dict) or not item.get("name"):
                continue
            name = str(item["name"]).strip()
            kind, vram_gb = _classify_gpu(name, float(item.get("vram") or 0))
            if kind != "integrated" and vram_gb == 0:
                vram_gb = _match_vram(name, registry_vram)
            if kind == "integrated":
                detail = "核显，共享系统内存"
            elif vram_gb == 0:
                detail = "显存未知（系统接口读数受限）"
            else:
                detail = f"显存 {vram_gb:g} GB"
            info.gpus.append(
                GpuInfo(
                    name=name,
                    vram_gb=vram_gb,
                    kind=kind,
                    detail=detail,
                    driver=str(item.get("driver") or "").strip(),
                )
            )

    # nvidia-smi 的数据更准确，用它覆盖同型号的估算值
    nvidia = _read_nvidia()
    if nvidia:
        others = [g for g in info.gpus if g.kind != "nvidia"]
        info.gpus = nvidia + others

    if not info.cpu_name or info.cpu_name == "未知":
        info.cpu_name = _read_cpu_from_registry() or " ".join(
            (os.environ.get("PROCESSOR_IDENTIFIER") or "未知 CPU").split()
        )
    if not info.cpu_threads:
        info.cpu_threads = os.cpu_count() or 0

    info.disk_total_gb, info.disk_free_gb = (round(v, 1) for v in _read_disk())
    info.cpu_usage = _read_cpu_usage()
    if not info.os_name:
        info.os_name = f"{sys.platform}"
    return info
