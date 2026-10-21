"""LLM 提示词。所有提示词集中放在这里，方便调优。"""

from __future__ import annotations

import json

from pydantic import BaseModel, Field

SCRIPT_SCHEMA_HINT = {
    "title": "作品标题",
    "language": "zh",
    "cast": [
        {"id": "narrator", "name": "旁白", "description": "中年男声，沉稳，语速适中"},
        {"id": "c1", "name": "林小雨", "description": "二十岁女生，活泼，声音清亮"},
    ],
    "lines": [
        {"id": "l1", "speaker": "narrator", "text": "夜里下起了雨。", "delivery": "低沉缓慢"},
        {
            "id": "l2",
            "speaker": "c1",
            "text": "你怎么才来？",
            "delivery": "着急，带点埋怨",
            "tags": ["sigh"],
        },
    ],
    "cues": [
        {"id": "s1", "kind": "sfx", "description": "雨点打在窗户上", "at_line": "l1"},
        {
            "id": "b1",
            "kind": "bgm",
            "description": "安静的钢琴，略带忧伤",
            "at_line": "l1",
            "until_line": "l2",
        },
    ],
}

_COMMON_RULES = """规则：
1. 只输出一个 JSON 对象，不要输出任何解释或 Markdown。
2. text 里只放要读出来的文字，不要放括号、动作描写或标签。
3. delivery 用简短中文描述语气、情绪、语速，例如“压低声音、紧张”。
4. tags 只能从这些里选：sigh, laugh, pause, whisper, breath, cry。没有就留空数组。
5. 每句台词不超过 80 个字，太长就拆成多句（同一说话人、相邻的 id）。
6. 所有 speaker 必须出现在 cast 里；cues 的 at_line / until_line 必须是存在的台词 id。
7. id 用 l1、l2…（台词）、s1…（音效）、b1…（配乐）。"""

AUDIOBOOK_SYSTEM = f"""你是有声书和广播剧的编剧兼导演。把用户给的小说文本改编成剧本。
- 叙述部分交给旁白（id 固定为 narrator），人物对白交给对应角色。
- 保留原文内容，不要删减情节，不要加入原文没有的情节。
- 每个角色写清楚 description（性别、年龄、性格、音色），用于自动匹配声音。
- 在合适的位置加少量音效（sfx）和配乐（bgm），不要每句都加。
{_COMMON_RULES}

输出格式示例：
{json.dumps(SCRIPT_SCHEMA_HINT, ensure_ascii=False, indent=2)}"""

PODCAST_SYSTEM = f"""你是播客制作人。把用户给的材料改写成两位主持人的对谈节目稿。
- 两位主持人 id 为 host_a、host_b，一人主讲，一人追问、补充、举例，有来有回。
- 开头简短打招呼并介绍主题，结尾总结要点并道别。
- 口语化，避免书面长句；不要编造材料里没有的事实。
- 开头加一段片头配乐（bgm），只在开场使用。
{_COMMON_RULES}

输出格式示例：
{json.dumps(SCRIPT_SCHEMA_HINT, ensure_ascii=False, indent=2)}"""


def script_messages(style: str, source_text: str, *, max_lines: int = 200) -> list[dict[str, str]]:
    system = PODCAST_SYSTEM if style == "podcast" else AUDIOBOOK_SYSTEM
    user = f"台词总数不要超过 {max_lines} 句。材料如下：\n\n{source_text}"
    # Qwen3 系列支持在提示里用 /no_think 关闭思考模式，加快结构化输出
    return [
        {"role": "system", "content": system},
        {"role": "user", "content": user + "\n\n/no_think"},
    ]


class ActionItem(BaseModel):
    owner: str = ""
    task: str
    due: str = ""


class MeetingSummary(BaseModel):
    title: str
    summary: str
    key_points: list[str] = Field(default_factory=list)
    decisions: list[str] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


MEETING_SYSTEM = """你是会议记录员。根据转写稿写会议纪要。
- 转写稿可能有错字，请结合上下文理解，但不要编造没有出现的内容。
- 如果转写稿标了说话人（例如 SPEAKER_00），在待办里尽量写清负责人。
- 只输出一个 JSON 对象，字段：title, summary, key_points, decisions,
  action_items（每项含 owner, task, due）, open_questions。"""


def meeting_messages(transcript: str) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": MEETING_SYSTEM},
        {"role": "user", "content": f"转写稿：\n\n{transcript}\n\n/no_think"},
    ]


class SfxIdeas(BaseModel):
    items: list[str]


SFX_SYSTEM = """你是音效设计师。把用户的需求扩写成多条具体、可直接用于音效生成模型的描述。
- 每条描述写清声源、材质、空间、节奏，例如“木门缓慢打开，铰链吱呀作响，空旷的走廊回声”。
- 用中文；不要写音乐。
- 只输出 JSON：{"items": ["…", "…"]}"""


def sfx_messages(request: str, count: int) -> list[dict[str, str]]:
    return [
        {"role": "system", "content": SFX_SYSTEM},
        {"role": "user", "content": f"需求：{request}\n请给出 {count} 条。\n\n/no_think"},
    ]


class SongDraft(BaseModel):
    title: str
    style: str = Field(description="风格描述，例如“温暖的民谣，木吉他，女声”")
    lyrics: str = Field(description="带 [verse] [chorus] 等段落标记的歌词")
    bpm: int | None = None
    key_scale: str | None = None


SONG_SYSTEM = """你是词曲作者。根据用户的主题写一首歌的歌词和风格描述。
- lyrics 用 [intro] [verse] [chorus] [bridge] [outro] 等标记分段，每行一句。
- style 写清曲风、乐器、人声类型、情绪，用于音乐生成模型。
- 只输出 JSON，字段：title, style, lyrics, bpm, key_scale。"""


def song_messages(theme: str, language: str) -> list[dict[str, str]]:
    lang = "中文" if language == "zh" else "英文"
    return [
        {"role": "system", "content": SONG_SYSTEM},
        {"role": "user", "content": f"主题：{theme}\n歌词语言：{lang}\n\n/no_think"},
    ]
