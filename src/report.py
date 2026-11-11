"""纯文本报告的生成与命令行输出，不依赖 Tkinter。"""

from __future__ import annotations

from datetime import datetime

from appinfo import VERSION
from evaluator import Evaluation, evaluate
from hardware import HardwareInfo, detect_hardware


def gpu_line(gpu) -> str:
    """把单块显卡格式化成一行文字，带上驱动版本。"""
    text = gpu.detail
    if gpu.driver:
        text += f"，驱动 {gpu.driver}"
    return f"{gpu.name}（{text}）"


def build_report(hw: HardwareInfo, ev: Evaluation) -> str:
    """把检测结果拼装成纯文本报告，供复制、保存或命令行输出。"""
    gpu_lines = "\n".join(f"                {gpu_line(g)}" for g in hw.gpus) or "                未检测到显卡"
    ram_line = f"{hw.ram_total_gb:g} GB"
    if hw.ram_speed_mhz or hw.ram_modules:
        ram_line += f"（{hw.ram_speed_mhz} MHz，{hw.ram_modules} 条）"
    cpu_line = f"{hw.cpu_cores} 核 {hw.cpu_threads} 线程 @ {hw.cpu_freq_mhz} MHz"
    if hw.cpu_usage >= 0:
        cpu_line += f"，当前占用 {hw.cpu_usage}%"
    disk_line = f"{hw.disk_free_gb:g} GB 可用 / 共 {hw.disk_total_gb:g} GB"
    if hw.disk_model:
        disk_line = f"{hw.disk_model}，{disk_line}"

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
        f"               {cpu_line}",
        f"  内存     ：{ram_line}",
        f"  显卡     ：{gpu_lines.strip()}",
        f"  磁盘     ：{disk_line}",
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
    """命令行模式：采集硬件、评估并打印报告。"""
    hw = detect_hardware()
    ev = evaluate(hw)
    print(build_report(hw, ev))
    return 0
