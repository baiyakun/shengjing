"""自动选角：根据角色描述，从音色库里挑最合适的声音。

目前是关键词打分（性别、年龄、风格），够用且可解释；
以后可以换成让 LLM 在候选音色里挑选。
"""

from __future__ import annotations

from typing import Any

from .script import CastMember

_GENDER = {
    "female": ("女", "女生", "女性", "少女", "姑娘", "母亲", "妈妈", "奶奶", "female", "woman"),
    "male": ("男", "男生", "男性", "少年", "小伙", "父亲", "爸爸", "爷爷", "male", "man"),
}
_AGE = {
    "child": ("孩子", "儿童", "小孩", "童声", "child", "kid"),
    "young": ("年轻", "少女", "少年", "二十", "学生", "青年", "young"),
    "middle": ("中年", "三十", "四十", "五十", "middle"),
    "old": ("老人", "老年", "老太", "老头", "老爷", "奶奶", "爷爷", "年迈", "六十", "七十",
            "old", "elderly"),
}


def _labels(text: str) -> set[str]:
    text = text.lower()
    found = set()
    for label, words in (*_GENDER.items(), *_AGE.items()):
        if any(w in text for w in words):
            found.add(label)
    # “男”“女”同时出现时（如“男女主角”）不做判断
    if {"female", "male"} <= found:
        found -= {"female", "male"}
    return found


def score(cast: CastMember, voice: dict[str, Any]) -> float:
    want = _labels(f"{cast.name} {cast.description}")
    have = _labels(" ".join([voice.get("name", ""), *voice.get("tags", [])]))
    s = 0.0
    for label in want:
        if label in have:
            s += 2.0 if label in _GENDER else 1.0
        elif label in _GENDER and (_GENDER.keys() - {label}) & have:
            s -= 3.0  # 性别不符扣分最重
    for tag in voice.get("tags", []):
        if tag and tag in cast.description:
            s += 0.5
    return s


def auto_cast(
    cast: list[CastMember], voices: list[dict[str, Any]], *, distinct: bool = True
) -> dict[str, str | None]:
    """返回 角色 ID → 音色 ID。已经指定 voice_id 的角色保持不变。"""
    result: dict[str, str | None] = {}
    used: set[str] = set()
    for member in cast:
        if member.voice_id:
            result[member.id] = member.voice_id
            used.add(member.voice_id)
    for member in cast:
        if member.id in result:
            continue
        ranked = sorted(voices, key=lambda v: score(member, v), reverse=True)
        choice = None
        for voice in ranked:
            if distinct and voice["id"] in used and len(used) < len(voices):
                continue
            choice = voice["id"]
            break
        result[member.id] = choice
        if choice:
            used.add(choice)
    return result
