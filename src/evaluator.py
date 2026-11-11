"""本地 AI 部署能力评估（只看等级，不推荐具体模型）。

估算方法参考 llm-checker / llmscan：
- 4-bit 量化下每个参数约占 0.58 字节，再乘推理框架开销得到实际占用；
- 独显按显存的 80% 作为可用预算，纯 CPU 按物理内存的 55%；
- 软预算内为「推荐」，硬预算内为「勉强」，超出为「不支持」。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from hardware import HardwareInfo

# 4-bit（Q4_K_M）量化下每个参数占用的 GB 数，取实测校准值
GB_PER_PARAM_Q4 = 0.58

# 推理框架额外开销（权重之外的内存/显存占用）
GPU_OVERHEAD = 1.15
CPU_OVERHEAD = 1.10

# 等级阶梯：(等级, 参数量级, 代表参数量, 适用场景)
TIERS = [
    ("入门级", "≤1.5B", 1.5, "简单问答、短句翻译、文本润色"),
    ("轻量级", "3B", 3.0, "日常对话、摘要、轻量写作，响应较快"),
    ("主流级", "7-8B", 8.0, "本地部署的甜点区间：写作、翻译、常识问答"),
    ("进阶级", "14B", 14.0, "长文理解、代码辅助、较复杂的推理任务"),
    ("专业级", "32B", 32.0, "专业领域问答、代码生成、多轮复杂任务"),
    ("旗舰级", "70B+", 70.0, "接近云端小模型体验，适合复杂推理与长文档"),
]

# 各等级实际能做到的事情
CAN_DO = {
    "入门级": [
        "短句翻译和单词、术语解释",
        "一两段文字的润色和改写",
        "简单问答、关键词和要点提取",
    ],
    "轻量级": [
        "日常聊天和常识问答",
        "起草邮件、文案、周报",
        "把一篇文章压缩成摘要",
        "整段中英文翻译",
        "解释简单的代码或报错信息",
    ],
    "主流级": [
        "较高质量的写作、翻译和润色",
        "多轮对话和角色设定",
        "代码补全、解释和简单重构",
        "长文摘要与要点提取",
    ],
    "进阶级": [
        "长文理解与结构化总结",
        "较复杂代码的编写与调试",
        "多步骤推理和数据分析思路",
        "多轮复杂任务（写方案、做对比）",
    ],
    "专业级": [
        "专业领域问答与文档处理",
        "完整项目级代码生成与审查",
        "复杂多轮任务与工具调用",
        "高质量长文写作",
    ],
    "旗舰级": [
        "接近云端小模型的回答质量",
        "复杂推理、长文档、代码工程",
        "除多模态外的大部分文本任务",
    ],
}

CANNOT_DO = {
    "入门级": [
        "连续多轮的复杂对话",
        "长文章摘要（容易漏重点）",
        "任何形式的代码辅助",
    ],
    "轻量级": [
        "复杂推理和数学计算，容易出错",
        "一次读几万字的超长文档",
        "专业级的代码生成和调试",
    ],
    "主流级": [
        "高难度推理题和精确计算",
        "超长文档的细节追踪",
        "法律、医学等专业领域的深度问答",
    ],
    "进阶级": [
        "十几万字的超大上下文，仍会丢细节",
        "对准确率要求极高的专业结论",
    ],
    "专业级": [
        "超长上下文叠加高并发使用",
        "本地训练或微调模型",
    ],
    "旗舰级": [
        "高并发的对外服务",
        "本地训练或微调模型",
    ],
}

POSITION = {
    "入门级": "只能当「文本小助手」",
    "轻量级": "适合当「轻量办公助手」",
    "主流级": "可以作为日常生产力工具",
    "进阶级": "能承担大部分日常文本和代码工作",
    "专业级": "接近可用的专业助手",
    "旗舰级": "本地体验接近云端小模型",
}

# 预估生成速度（token/s），按代表参数量分档
CPU_SPEED = {1.5: "15-28", 3.0: "8-15", 8.0: "3-5", 14.0: "1.5-3", 32.0: "<1", 70.0: "不可用"}
GPU_SPEED = {1.5: "80-130", 3.0: "50-90", 8.0: "25-40", 14.0: "14-24", 32.0: "7-12", 70.0: "3-6"}


@dataclass
class TierOption:
    """单个等级在当前硬件下的适配结果。"""

    label: str
    params_desc: str
    params_b: float
    need_gb: float
    ratio: float  # 占资源池的百分比
    verdict: str  # 推荐 / 可用 / 勉强 / 不支持
    reason: str  # 判定理由码
    speed: str
    speed_level: str  # 很快 / 可用 / 偏慢 / 太慢 / 跑不动
    scene: str


@dataclass
class Evaluation:
    """整机评估结论。"""

    deployable: bool
    tier: str
    tier_desc: str
    score: int
    path: str
    pool_gb: float
    pool_label: str
    rec_line: float  # 推荐线（百分比）
    ok_line: float  # 可用线（百分比）
    headline: str
    best: TierOption | None
    tiers: list[TierOption] = field(default_factory=list)
    usage_tags: list[tuple[str, str]] = field(default_factory=list)
    advice: list[str] = field(default_factory=list)


def _budget(hw: HardwareInfo) -> tuple[str, float, str, bool, float, float]:
    """确定推理路径与资源池，返回 (路径, 池大小, 池名称, 是否 GPU, 推荐线, 可用线)。

    CPU 推理受内存带宽限制，阈值更保守；GPU 可以多压一点。
    """
    gpu = hw.dgpu
    if gpu and gpu.vram_gb >= 4:
        if gpu.kind == "nvidia":
            return f"GPU 加速（{gpu.name}）", gpu.vram_gb, "显存", True, 0.75, 0.85
        return f"GPU 加速（{gpu.name}，Vulkan）", gpu.vram_gb, "显存", True, 0.70, 0.80

    ram = hw.ram_total_gb
    reason = "CPU 推理（未检测到可用的独立显卡）" if not gpu else "CPU 推理（核显不参与计算）"
    return reason, ram, "内存", False, 0.55, 0.65


def _speed_level(speed: str, is_gpu: bool) -> str:
    """把速度区间折算成体感档位（CPU 推理的可用门槛更低）。"""
    if speed == "不可用":
        return "跑不动"
    if speed.startswith("<"):
        low = 0.5
    else:
        low = float(speed.split("-")[0])
    slow, ok, fast = (4, 10, 30) if is_gpu else (3, 6, 15)
    if low < slow:
        return "太慢"
    if low < ok:
        return "偏慢"
    if low < fast:
        return "可用"
    return "很快"


def _usage_tags(tier: str | None) -> list[tuple[str, str]]:
    """按等级给出用途标签，✔ 可用、△ 勉强、✘ 不适合。"""
    matrix = {
        "入门级": ["✔", "△", "✘", "✘"],
        "轻量级": ["✔", "✔", "△", "✘"],
        "主流级": ["✔", "✔", "✔", "△"],
        "进阶级": ["✔", "✔", "✔", "✔"],
        "专业级": ["✔", "✔", "✔", "✔"],
        "旗舰级": ["✔", "✔", "✔", "✔"],
    }
    marks = matrix.get(tier or "", ["✘", "✘", "✘", "✘"])
    names = ["聊天问答", "写作翻译", "代码辅助", "长文档处理"]
    return list(zip(names, marks))


def evaluate(hw: HardwareInfo) -> Evaluation:
    """根据硬件信息判断最高可运行的模型等级。"""
    path, pool, pool_label, is_gpu, rec_line, ok_line = _budget(hw)
    overhead = GPU_OVERHEAD if is_gpu else CPU_OVERHEAD
    speed_table = GPU_SPEED if is_gpu else CPU_SPEED

    tiers: list[TierOption] = []
    for label, params_desc, params_b, scene in TIERS:
        need = params_b * GB_PER_PARAM_Q4 * overhead
        ratio = need / pool if pool else 9.99
        if ratio <= rec_line:
            verdict = "推荐"
            reason = f"占用 {ratio * 100:.0f}%，余量充足"
        elif ratio <= ok_line:
            verdict = "可用"
            reason = f"占用 {ratio * 100:.0f}%，余量偏少"
        elif ratio <= 0.98:
            verdict = "勉强"
            reason = "需部分卸载到内存" if is_gpu else f"占用 {ratio * 100:.0f}%，接近上限"
        else:
            verdict = "不支持"
            reason = f"占用 {ratio * 100:.0f}%，超出上限"
        speed = speed_table.get(params_b, "未知")
        tiers.append(
            TierOption(
                label=label,
                params_desc=params_desc,
                params_b=params_b,
                need_gb=round(need, 1),
                ratio=round(ratio * 100, 1),
                verdict=verdict,
                reason=reason,
                speed=speed,
                speed_level=_speed_level(speed, is_gpu),
                scene=scene,
            )
        )

    recommended = [t for t in tiers if t.verdict == "推荐"]
    usable = [t for t in tiers if t.verdict == "可用"]
    tight = [t for t in tiers if t.verdict == "勉强"]

    penalty = 1.0
    if recommended:
        best = recommended[-1]
        headline = f"适合本地部署 · 最高等级：{best.label}（{best.params_desc} 参数）"
    elif usable:
        best = usable[-1]
        penalty = 0.85
        headline = f"可以运行 {best.label}（{best.params_desc} 参数），但资源余量偏少，体验一般"
    elif tight:
        best = tight[-1]
        penalty = 0.7
        headline = f"只能勉强运行 {best.label}（{best.params_desc} 参数），建议先关掉其他占用资源的程序"
    else:
        best = None
        penalty = 0
        headline = "当前电脑无法在本地运行 1.5B 级别的模型"

    if best:
        deployable = True
        ratio = math.log10(1 + best.params_b) / math.log10(71)
        score = int(round(100 * ratio * penalty))
        tier = best.label
        tier_desc = best.scene
    else:
        deployable = False
        score = 5
        tier = "无法部署"
        tier_desc = "可用内存不足，连最小的量化模型也放不下"

    advice = _build_capability(hw, best, is_gpu)

    return Evaluation(
        deployable=deployable,
        tier=tier,
        tier_desc=tier_desc,
        score=score,
        path=path,
        pool_gb=round(pool, 1),
        pool_label=pool_label,
        rec_line=round(rec_line * 100),
        ok_line=round(ok_line * 100),
        headline=headline,
        best=best,
        tiers=tiers,
        usage_tags=_usage_tags(tier),
        advice=advice,
    )


def _speed_note(best: TierOption, is_gpu: bool) -> str:
    """按推理路径给出速度体感描述。"""
    if best.speed == "不可用":
        return "实际体感：这个等级在本机跑不起来，只能看不能用。"
    if is_gpu:
        return f"实际体感：生成速度大约 {best.speed} token/s，回答基本即时出现，可以连续对话。"
    if best.params_b <= 3:
        return (
            f"实际体感：生成速度大约 {best.speed} token/s，接近正常阅读速度，"
            "回答一段话通常等几秒到十几秒。"
        )
    return (
        f"实际体感：生成速度大约 {best.speed} token/s，等待比较明显，"
        "适合让它一次把整个任务做完，不适合连续聊天。"
    )


def _upgrade_note(hw: HardwareInfo, best: TierOption, is_gpu: bool) -> str:
    """给出升到下一档的建议。"""
    labels = [t[0] for t in TIERS]
    idx = labels.index(best.label)
    if idx >= len(labels) - 1:
        return "想更进一步：本机已能覆盖全部等级，剩下的瓶颈是速度而不是能不能跑。"
    nxt_label = labels[idx + 1]
    nxt_desc = next(t[1] for t in TIERS if t[0] == nxt_label)
    if is_gpu and hw.dgpu:
        return f"想更进一步：换显存更大的显卡就能上到 {nxt_label}（{nxt_desc} 参数）。"
    if hw.ram_total_gb < 16:
        return (
            f"想更进一步：内存加到 16 GB（双通道）更容易上到 {nxt_label}（{nxt_desc} 参数），"
            "换 8 GB 以上显存的独立显卡效果更明显。"
        )
    return f"想更进一步：换 8 GB 以上显存的独立显卡就能上到 {nxt_label}（{nxt_desc} 参数）。"


def _build_capability(hw: HardwareInfo, best: TierOption | None, is_gpu: bool) -> list[str]:
    """按最高等级说明这台电脑实际能用 AI 做到什么程度。"""
    gpu_desc = f"独立显卡 {hw.dgpu.name}" if hw.dgpu else "无独立显卡"
    head = f"当前配置（{hw.cpu_name}、{hw.ram_total_gb:g} GB 内存、{gpu_desc}）"

    if best is None:
        return [
            f"总体判断：{head}可用内存太低，连 1.5B 级别的小模型都装不下，暂时不具备本地运行 AI 的条件。",
            "内存加到 8 GB 以上后，可以运行入门级或轻量级模型，完成翻译、润色、摘要这类文字工作。",
        ]

    tips = [f"总体判断：{head}{POSITION[best.label]}。"]

    can = CAN_DO.get(best.label, [])
    cannot = CANNOT_DO.get(best.label, [])
    if can:
        tips.append("可以胜任：" + "；".join(can) + "。")
    if cannot:
        tips.append("会比较吃力：" + "；".join(cannot) + "。")

    tips.append(_speed_note(best, is_gpu))

    if is_gpu and hw.dgpu:
        vram = hw.dgpu.vram_gb
        if vram - best.need_gb >= best.need_gb * 0.5:
            tips.append(
                f"资源占用：模型约占显存 {best.need_gb:g} GB，显卡共 {vram:g} GB，"
                "还有余量，可以同时开着浏览器等程序。"
            )
        else:
            tips.append(
                f"资源占用：模型约占显存 {best.need_gb:g} GB，显卡共 {vram:g} GB，"
                "余量不多，跑大任务时建议先关掉占显存的程序。"
            )
    else:
        avail = hw.ram_available_gb
        if avail >= best.need_gb * 1.6:
            tips.append(
                f"资源占用：运行时约占 {best.need_gb:g} GB 内存，检测时还有 {avail:g} GB 可用，"
                "可以同时开浏览器或办公软件。"
            )
        else:
            tips.append(
                f"资源占用：运行时约占 {best.need_gb:g} GB 内存，检测时只剩 {avail:g} GB 可用，"
                "余量紧张，用之前建议先关掉浏览器等占内存的程序。"
            )

    if hw.disk_free_gb:
        if hw.disk_free_gb >= best.need_gb * 2:
            tips.append(f"磁盘空间：模型文件约 {best.need_gb:g} GB，系统盘还剩 {hw.disk_free_gb:g} GB，空间充足。")
        else:
            tips.append(
                f"磁盘空间：模型文件约 {best.need_gb:g} GB，系统盘只剩 {hw.disk_free_gb:g} GB，"
                "建议先清理到两倍以上空间。"
            )

    tips.append("需要说明：本地只能「运行」模型，训练或微调需要远高于此的算力，本机不适合。")
    tips.append(_upgrade_note(hw, best, is_gpu))
    return tips
