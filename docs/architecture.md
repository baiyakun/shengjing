# 架构说明

## 进程和通信

```
Tauri 外壳（Rust）
  └─ 启动并守护 → 主引擎（python -m shengjing_engine）
                    └─ 按需启动和关闭 → 语音进程 / LLM 进程 / 分析进程 / 音乐进程
```

- **外壳 → 主引擎**：外壳生成随机令牌，通过环境变量 `SHENGJING_ENGINE_TOKEN` 传给引擎；
  引擎监听 `127.0.0.1` 的随机端口，在标准输出打印 `{"event": "ready", "port": N}`。
  外壳读到后把地址和令牌告诉界面（Tauri 命令 `engine_info`）。
  引擎意外退出时外壳自动重启（1、2、4、8、16、32 秒退避，连续 5 次失败后等用户手动重试）。
  退出 App 时外壳先发 SIGTERM，引擎会先关闭所有模型进程再退出，10 秒后仍未退出则强制结束。
- **界面 → 主引擎**：HTTP（`Authorization: Bearer <令牌>`）；任务进度通过 WebSocket `/events` 推送；
  `<audio>` 标签通过 `/files?path=...&token=...` 播放数据目录里的文件。
- **主引擎 → 模型进程**：本机 HTTP，同一个令牌。我们自己写的进程（语音、分析）遵循
  kit 协议（`workers/worker-kit`），mlx-lm 和 ACE-Step 直接用它们自带的服务。

代码：`app/src-tauri/src/engine.rs`、`engine/src/shengjing_engine/core/workers.py`。

## 内存调度

`core/planner.py` 是纯逻辑，`core/scheduler.py` 负责执行。加载一个模型前：

1. 已加载就直接用。
2. 内存不够时按“最久没用”的顺序腾空间；正在使用的模型不能动。
3. 被腾出的如果是某个进程里的最后一个模型，就直接关闭整个进程（唯一能保证内存全部释放的办法）。
4. LLM 和音乐进程一个进程只放一个模型，换模型就重启进程。
5. 腾完还不够就报错，告诉用户需要多少、可用多少。

预算 = 物理内存 − 预留（默认 4GB 或 20%，取较大值）。每个模型的内存估算在 `core/catalog.py`。
空闲超过设定时间（默认 10 分钟）的进程会被自动关闭。

## 在线 API

在线 API 和本地模型登记在同一个模型目录里（`core/catalog.py`，`worker="cloud"`，
`source` 为服务商 ID）。调度器遇到在线模型时不启动本地进程、不占内存预算，
直接返回 `CloudHandle`。它和本地模型的句柄接口相同（`run(op, params)`），
所以工作流不需要区分本地和在线：

| 操作 | 本地模型 | 在线 API（按模型类型转到） |
| --- | --- | --- |
| `tts.generate` | 语音进程里的 mlx-speech 模型 | `tts()`；音效类型转 `sfx()`；对话类型转 `dialogue()` |
| `asr.transcribe` | 语音进程里的 ASR 模型 | `asr()`，可带 `diarize` 区分说话人 |
| `music.generate` | ACE-Step 服务 | `music()` |

服务商实现在 `providers/`：`openai_compat.py`、`senseaudio.py`、`fish_audio.py`、`elevenlabs.py`。
返回的音频统一转成 WAV，方便后续质检和混音。需要先克隆的服务（SenseAudio、ElevenLabs）
会把“参考录音指纹 → 服务商音色 ID”记在 `cloud_voices` 表里，避免重复克隆。

剧本编译器也给每个在线 TTS 准备了写法：OpenAI 的语气描述放进 `instructions`，
Fish Audio 沿用行内标签，ElevenLabs 用 v3 音频标签，SenseAudio 音频生成用
“角色列表 → `{speaker：角色名}`（语气）台词”的格式，每段不超过约 120 秒。

## 任务队列

所有生成任务存在 SQLite 里，排成一条队按顺序执行，一次只跑一个，内存占用可预测。
引擎重启后，上次没跑完的任务自动重新排队。任务可以取消（在步骤之间检查）。

工作流在 `workflows/`，每个是一个 `async def xxx(ctx) -> dict`：

| 任务类型 | 说明 |
| --- | --- |
| `script.generate` | 原文 → LLM 剧本 → 自动选角 → 保存项目 |
| `audiobook.render` | 剧本 → 逐句配音 → 质检重做 → 音效配乐 → 混音 → 导出音频和字幕 |
| `tts.quick` | 一句话配音 |
| `sfx.generate` | 生成音效（可让 LLM 扩写描述） |
| `audio.edit` | Step-Audio-EditX 录音编辑 |
| `voice.transcribe` | 给音色的参考录音生成文字稿 |
| `asr.transcribe` / `meeting.summarize` | 转写（可区分说话人）/ 会议纪要 |
| `music.generate` | ACE-Step 生成音乐 |
| `extension.install` | 安装扩展包 |

## 统一剧本和编译器

LLM 只输出 `orchestration/script.py` 定义的剧本：角色（含描述和音色）、台词（文字、语气描述、
标签、目标时长、句后停顿）、音效和配乐提示。`orchestration/compiler.py` 把它翻译成各模型的写法：

| 模型 | 语气 | 标签 | 多人 | 时长 |
| --- | --- | --- | --- | --- |
| Fish S2 Pro | `[语气]` 前缀 | `[sigh]` `[laughing]` 等 | 逐句 | 不支持 |
| Drama 3 | `[语气]` 前缀 | `[sighs]` `<whisper>…</whisper>` | `<\|speaker:N\|>`，最多 16 人 | `duration_seconds` |
| MOSS-TTSD | 不支持 | 不支持 | `[S1]` `[S2]` | 不支持 |
| VibeVoice | 不支持 | 不支持 | `Speaker 1:`，最多 4 人 | 不支持 |
| Breeze TTS 2 | `instruction` 参数 | `(sigh)` `(laugh)` | 逐句 | 不支持 |
| DramaBox | 英文场景描述 | 不支持 | 逐句 | `duration_seconds` |
| dots.tts 等 | 不支持 | 不支持 | 逐句 | 不支持 |

不支持的部分会被忽略并给出提示，显示在界面上。

## 质检回听

逐句生成后，用 ASR 转写回文字，按字（中文）/ 词（英文）计算错误率，超过 12% 或时长偏差超过 15%
判为不合格。不合格的句子换随机种子重做（默认最多 2 轮），所有版本都保留，自动选用最好的一个。
TTS 和 ASR 分批执行，减少模型切换。

## 数据目录

`~/Library/Application Support/dev.shengjing.desktop/`

```
shengjing.sqlite3   任务、音色、项目、逐句版本
settings.json       设置（密钥在系统钥匙串里）
voices/<id>/        参考录音
projects/<id>/      逐句音频、音效、配乐
outputs/<task>/     任务输出
models/             模型下载缓存（HF_HOME）
extensions/         扩展包（analysis/、music/）
logs/               引擎和各进程日志
```
