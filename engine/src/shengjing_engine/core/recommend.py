"""按这台机器推荐每类任务的默认模型。

推荐依据：
1. 内存：模型要放得进“留给模型的内存”（总内存减去给系统预留的部分）。
   经常同时用到的模型（例如逐句配音时的语音合成和质检用的转写）最好能一起放下，
   放不下也能运行，但每处理一句都要换一次模型，会慢很多。
2. LLM 最多占模型内存的一半，给语音模型留出空间。
3. 芯片：基础款芯片（不带 Pro / Max / Ultra）算力较弱，同类模型里优先用更快的版本。
4. 版本：商用版只在允许商用的模型里选。
5. 本地模型放不下、或者需要的扩展包还没装，而对应的在线服务已经连接时，推荐在线服务。

需要自己转换权重的模型不会被自动推荐。用户在设置里选定的模型优先于这里的推荐。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass

from . import catalog
from .planner import WORKER_OVERHEAD_GB

KIND_LABEL: dict[str, str] = {
    "tts": "朗读",
    "dialogue": "多人对话",
    "voice_design": "声音设计",
    "edit": "录音编辑",
    "sfx": "音效",
    "asr": "转写",
    "asr_stream": "实时转写",
    "llm": "LLM",
    "diarization": "说话人区分",
    "diarization_stream": "实时说话人区分",
    "alignment": "时间戳对齐",
    "separation": "人声分离",
    "vad": "人声检测",
    "music": "音乐",
}

# 每类任务的候选模型，靠前的效果更好。没列出的类别按目录顺序。
PREFERENCE: dict[str, tuple[str, ...]] = {
    "tts": (
        "fish-s2-pro", "dots-tts-soar", "dots-tts-mf", "fireredtts3-base", "moss-local",
        "longcat",
    ),
    "dialogue": ("moss-ttsd", "vibevoice"),
    "asr": ("qwen3-asr-1.7b", "cohere-asr", "granite-speech-4.0-1b"),
    "asr_stream": ("confucius4-r2t2", "nemotron-asr-streaming"),
    "llm": ("qwen3.6-35b-a3b-4bit", "qwen3.5-9b-4bit", "qwen3-4b-4bit"),
}

# 基础款芯片上优先用的更快版本：慢的 → 快的
FASTER: dict[str, str] = {"dots-tts-soar": "dots-tts-mf"}

# 经常和这类模型同时在内存里的模型：(类别, 叫法, 简称)
COMPANIONS: dict[str, tuple[str, str, str]] = {
    "tts": ("asr", "质检用的转写模型", "转写模型"),
    "dialogue": ("asr", "质检用的转写模型", "转写模型"),
    "asr_stream": ("llm", "翻译用的 LLM", "LLM"),
}
# 只有开着质检回听时，语音合成才需要和转写模型一起放下
QA_COMPANION_KINDS = ("tts", "dialogue")

LLM_SHARE = 0.5

# 先定下被别人依赖的类别（转写、LLM），再推荐依赖它们的类别
ORDER: tuple[str, ...] = (
    "asr", "llm", "tts", "dialogue", "asr_stream", "sfx", "music", "edit", "voice_design",
    "diarization", "diarization_stream", "alignment", "separation", "vad",
)

EXTENSION_LABEL = {"analysis": "分析扩展包", "music": "音乐扩展包"}


@dataclass(frozen=True)
class Machine:
    total_gb: float
    budget_gb: float
    """留给模型的内存。"""
    chip: str = "unknown"

    @property
    def chip_class(self) -> str | None:
        """base / pro / max / ultra；认不出芯片时为 None。"""
        m = re.search(r"\bM\d+(?:\s+(Pro|Max|Ultra))?\b", self.chip)
        if not m:
            return None
        return (m.group(1) or "base").lower()


@dataclass(frozen=True)
class Recommendation:
    kind: str
    model: str | None
    """None 表示这个版本没有可用的模型。"""
    reason: str
    fits: bool
    """推荐的模型能放进这台机器的内存（在线服务总是 True）。"""

    def to_dict(self) -> dict:
        return asdict(self)


def gb(x: float) -> str:
    return f"{x:.1f}".removesuffix(".0") + " GB"


def need_gb(models: Iterable[catalog.ModelSpec]) -> float:
    """同时加载这些模型大约要多少内存（同一个进程只算一份进程开销）。"""
    local = [m for m in models if m.worker != "cloud"]
    return sum(m.memory_gb for m in local) + WORKER_OVERHEAD_GB * len({m.worker for m in local})


def fits(spec: catalog.ModelSpec, machine: Machine) -> bool:
    return spec.worker == "cloud" or need_gb([spec]) <= machine.budget_gb


def recommend(
    machine: Machine,
    edition: catalog.Edition,
    *,
    worker_ready: Callable[[str], bool],
    provider_ready: Callable[[str], bool],
    qa_enabled: bool = True,
    in_use: dict[str, str] | None = None,
) -> dict[str, Recommendation]:
    """in_use：设置里已经选定的模型（空字符串表示用在线服务），
    用来判断哪些模型会和推荐的模型同时在内存里。"""
    out: dict[str, Recommendation] = {}
    for kind in ORDER:
        out[kind] = _pick(kind, machine, edition, out, worker_ready, provider_ready, qa_enabled,
                          in_use or {})
    return out


def _candidates(kind: str, edition: catalog.Edition, machine: Machine) -> list[catalog.ModelSpec]:
    ids = PREFERENCE.get(kind) or tuple(
        m.id for m in catalog.all_models() if m.kind == kind and m.worker != "cloud"
    )
    specs = [catalog.get(i) for i in ids]
    specs = [s for s in specs if s.allowed_in(edition) and not s.needs_conversion]
    if edition == "commercial":
        # 商用版优先选可以直接商用的，有条件商用的放后面
        specs.sort(key=lambda s: s.commercial != "yes")
    if machine.chip_class == "base":
        ids_now = [s.id for s in specs]
        for slow, fast in FASTER.items():
            if slow in ids_now and fast in ids_now and ids_now.index(slow) < ids_now.index(fast):
                i, j = ids_now.index(slow), ids_now.index(fast)
                specs[i], specs[j] = specs[j], specs[i]
                ids_now[i], ids_now[j] = ids_now[j], ids_now[i]
    return specs


def _cloud(kind: str, edition: catalog.Edition, provider_ready: Callable[[str], bool]):
    for m in catalog.all_models():
        if (m.kind == kind and m.worker == "cloud" and m.allowed_in(edition)
                and provider_ready(m.source)):
            return m
    return None


def _pick(
    kind: str,
    machine: Machine,
    edition: catalog.Edition,
    done: dict[str, Recommendation],
    worker_ready: Callable[[str], bool],
    provider_ready: Callable[[str], bool],
    qa_enabled: bool,
    in_use: dict[str, str],
) -> Recommendation:
    label = KIND_LABEL.get(kind, kind)
    budget = machine.budget_gb
    local = _candidates(kind, edition, machine)
    cloud = _cloud(kind, edition, provider_ready)

    if not local:
        if cloud:
            return Recommendation(kind, cloud.id, f"没有本地{label}模型，用已连接的在线服务", True)
        edition_label = "商用版" if edition == "commercial" else "个人版"
        return Recommendation(kind, None, f"{edition_label}没有可用的{label}模型", False)

    if kind == "llm":
        return _pick_llm(local, machine)

    # 一起用的模型（只算本地的；在线服务不占内存）
    companions: list[catalog.ModelSpec] = []
    partner = short = ""
    if kind in COMPANIONS and (qa_enabled or kind not in QA_COMPANION_KINDS):
        other, partner, short = COMPANIONS[kind]
        rec = done.get(other)
        model_id = in_use[other] if other in in_use else (rec.model if rec else None)
        if model_id in catalog.CATALOG and catalog.get(model_id).worker != "cloud":
            companions = [catalog.get(model_id)]

    together = next((m for m in local if need_gb([m, *companions]) <= budget), None)
    alone = next((m for m in local if need_gb([m]) <= budget), None)
    chosen = together or alone

    if chosen is None:
        smallest = min(local, key=lambda m: m.memory_gb)
        why = f"本地{label}模型至少要 {gb(need_gb([smallest]))}，可用只有 {gb(budget)}"
        if cloud:
            return Recommendation(kind, cloud.id, f"{why}，改用已连接的在线服务", True)
        return Recommendation(kind, smallest.id, f"{why}，可能会内存不足；可以改用在线服务",
                              False)

    if not worker_ready(chosen.worker) and cloud:
        ext = EXTENSION_LABEL.get(chosen.worker, f"{chosen.worker} 进程")
        return Recommendation(kind, cloud.id, f"{ext}还没安装，先用已连接的在线服务", True)

    if companions and chosen is together:
        gap = " " if partner[-1].isascii() else ""
        total = gb(need_gb([chosen, *companions]))
        parts = [f"连同{partner}{gap}一起约 {total}，可用 {gb(budget)}"]
    elif companions:
        parts = [f"约 {gb(need_gb([chosen]))}；放不下{partner}，每句都要换模型，会慢一些"]
    else:
        parts = [f"约 {gb(need_gb([chosen]))}，可用 {gb(budget)}"]
    first = local[0]
    if chosen.id != first.id:
        with_partner = f"（连同{short}）" if companions else ""
        parts.append(f"{first.name}{with_partner}要 {gb(need_gb([first, *companions]))}，放不下")
    if machine.chip_class == "base" and chosen.id in FASTER.values():
        parts.append("基础款芯片优先用更快的版本")
    if not worker_ready(chosen.worker):
        parts.append(f"需要先安装{EXTENSION_LABEL.get(chosen.worker, chosen.worker)}")
    return Recommendation(kind, chosen.id, "；".join(parts), True)


def _pick_llm(local: list[catalog.ModelSpec], machine: Machine) -> Recommendation:
    budget = machine.budget_gb
    cap = budget * LLM_SHARE
    chosen = next((m for m in local if need_gb([m]) <= cap), None)
    if chosen:
        return Recommendation(
            "llm", chosen.id,
            f"约 {gb(need_gb([chosen]))}，不超过可用内存的一半，给语音模型留出空间", True,
        )
    smallest = min(local, key=lambda m: m.memory_gb)
    if need_gb([smallest]) <= budget:
        return Recommendation("llm", smallest.id,
                              f"约 {gb(need_gb([smallest]))}；内存比较紧，选最小的 LLM", True)
    return Recommendation(
        "llm", smallest.id,
        f"最小的本地 LLM 也要 {gb(need_gb([smallest]))}，可用只有 {gb(budget)}；建议改用云端 LLM",
        False,
    )
