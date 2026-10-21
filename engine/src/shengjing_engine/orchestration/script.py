"""统一剧本格式。

LLM 只输出这一种格式；compiler.py 再把它翻译成各个 TTS 模型自己的写法。
这样换模型、加模型都不用改提示词。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

# 统一的点状标签。编译器会把它们翻译成各模型的写法
CanonicalTag = Literal["sigh", "laugh", "pause", "whisper", "breath", "cry"]


class CastMember(BaseModel):
    id: str = Field(description="角色 ID，例如 narrator、c1")
    name: str
    description: str = Field(default="", description="性别、年龄、性格、音色特点，用于匹配音色")
    voice_id: str | None = None


class Line(BaseModel):
    id: str
    speaker: str = Field(description="CastMember.id")
    text: str = Field(description="要读出来的文字，不含任何标签")
    delivery: str = Field(default="", description="自然语言语气描述，例如“低声、带着哭腔”")
    tags: list[CanonicalTag] = Field(default_factory=list)
    duration: float | None = Field(default=None, description="目标时长（秒），配音卡时间用")
    pause_after: float = Field(default=0.35, ge=0.0, le=10.0)


class Cue(BaseModel):
    id: str
    kind: Literal["sfx", "bgm"]
    description: str = Field(description="音效或配乐的描述")
    at_line: str | None = Field(default=None, description="从哪一句开始，None 表示开头")
    until_line: str | None = Field(default=None, description="配乐持续到哪一句结束")
    duration: float | None = None
    volume_db: float | None = None


class Script(BaseModel):
    title: str
    language: Literal["zh", "en"] = "zh"
    cast: list[CastMember]
    lines: list[Line]
    cues: list[Cue] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_refs(self) -> Script:
        cast_ids = {c.id for c in self.cast}
        if len(cast_ids) != len(self.cast):
            raise ValueError("角色 ID 有重复")
        line_ids = [line.id for line in self.lines]
        if len(set(line_ids)) != len(line_ids):
            raise ValueError("台词 ID 有重复")
        for line in self.lines:
            if line.speaker not in cast_ids:
                raise ValueError(f"台词 {line.id} 的说话人 {line.speaker} 不在角色列表里")
            if not line.text.strip():
                raise ValueError(f"台词 {line.id} 是空的")
        known = set(line_ids)
        for cue in self.cues:
            for ref in (cue.at_line, cue.until_line):
                if ref is not None and ref not in known:
                    raise ValueError(f"音效/配乐 {cue.id} 引用了不存在的台词 {ref}")
        return self

    def cast_by_id(self) -> dict[str, CastMember]:
        return {c.id: c for c in self.cast}
