<div align="center">

# 声境 Shengjing

**在 Mac 上本地运行的 AI 有声内容工作室**

有声书、AI 播客、声音分身、会议纪要、录音后期、音效、音乐。默认全部在你的 Mac 上本地完成；
每类能力也可以切换到在线 API（OpenAI 兼容接口、SenseAudio、Fish Audio、ElevenLabs）。

</div>

> 仅供个人研究使用。

## 目录

- [功能](#功能)
- [架构](#架构)
- [目录结构](#目录结构)
- [开发](#开发)
- [打包发布](#打包发布)
- [在线 API](#在线-api)
- [使用的开源框架与模型](#使用的开源框架与模型)
- [许可证注意事项](#许可证注意事项)
- [合规](#合规)
- [本项目的许可证](#本项目的许可证)

## 功能

| 应用 | 做什么 | 状态 |
| --- | --- | --- |
| 有声书 / 广播剧 | 小说 → LLM 拆角色、标语气、加音效配乐 → 逐句配音 → 质检回听 → 混音导出音频和字幕 | 已实现，待 Mac 验证 |
| AI 播客 | 文章 → LLM 写双人对谈稿 → 配音 → 片头音乐 → 响度标准化 | 已实现，待 Mac 验证 |
| 声音分身 / 音色库 | 导入参考录音（必须确认授权）→ 自动转写文字稿 → 用你的声音朗读任意文字 | 已实现，待 Mac 验证 |
| 会议纪要 | 录音或视频 → 区分发言人 → 转写 → LLM 写纪要和待办 | 已实现；区分发言人需要分析扩展包 |
| 录音后期 | 改情绪、风格、语速，降噪，去静音，加笑声叹气 | 部分实现；按时间轴找口误、单句替换在第 3 阶段 |
| 音效库 | 一句需求 → LLM 扩写成多条描述 → 批量生成音效 | 已实现，待 Mac 验证 |
| 音乐 | 主题 → LLM 写歌词和风格 → 生成歌曲或纯配乐 | 已实现；需要音乐扩展包 |
| 翻译配音 | 视频 → 分离人声 → 转写对齐 → LLM 等时翻译 → 原声克隆按时长配音 → 混音回写 | 第 4 阶段（分析进程已支持分离和对齐） |
| 同声传译 | 实时转写 → LLM 翻译 → 字幕，可选语音播报 | 第 4 阶段（语音进程已支持流式转写） |

几个贯穿所有应用的设计：

- **统一剧本格式**：LLM 只输出一种 JSON 剧本，编译器再翻译成各 TTS 模型自己的标签写法
  （Fish 的 `[whisper]`、Drama 3 的 `<|speaker:N|>`、MOSS-TTSD 的 `[S1]` 等）。
- **质检回听**：生成的每句话都用 ASR 转写回文字和原稿比对，吞字、错字自动重做，保留最好的版本。
- **本地和在线随时切换**：语音合成、转写、音效、音乐、多人对话都可以选本地模型或在线 API，
  每个页面单独选，也可以在设置里设默认。
- **定位个人使用**：默认是个人版，可以使用所有模型（包括非商用许可的模型）。
  保留了商用版开关，打开后会自动排除非商用许可的模型。
- **按内存调度**：根据机器内存决定哪些模型常驻、哪些用完就关闭进程释放内存。

## 架构

对用户来说是一个 macOS App、一个安装包。内部按运行环境分成几个进程，原因是：
Mac 上模型和系统共用一块内存，关闭进程是唯一能保证内存全部释放的办法；
某个模型崩溃不会连累界面和任务队列；ACE-Step 要求 Python 3.12，而 mlx-speech 要求 3.13。

```
┌──────────────── macOS App（Tauri 2 + React）────────────────┐
│ 9 个应用页面 · 剧本编辑 · 音色库 · 模型管理 · 任务中心 · 设置   │
└──────────────────────────┬─────────────────────────────────┘
          主引擎（Python 3.13，常驻）：任务队列、内存调度、
          项目库、音色库、LLM 编排、剧本编译、质检、混音导出
                           │ 本机 HTTP + 令牌，按需启动和关闭
    ┌─────────────┬────────┴─────┬───────────────┐
  语音进程        LLM 进程        分析进程          音乐进程
  mlx-speech     mlx-lm          PyTorch          ACE-Step 1.5
  TTS/ASR/音效   本地大模型       说话人/时间戳/分离  歌曲和配乐
 └── 基础环境 Py3.13（随 App）─┘  扩展包 Py3.13     扩展包 Py3.12
```

详细设计见 [docs/architecture.md](docs/architecture.md)。

## 目录结构

```
app/                 macOS App
  src/               React 界面（9 个应用页面 + 系统页面）
  src-tauri/         Rust 外壳：启动和守护主引擎、文件对话框、麦克风权限
engine/              主引擎（Python）
  src/shengjing_engine/
    core/            模型目录、内存规划、进程管理、任务队列、存储、扩展包
    orchestration/   LLM 调用、统一剧本、剧本编译器、自动选角、质检、提示词
    audio/           ffmpeg 编解码、混音、切段、字幕
    workflows/       各应用的处理流程
    api/             给界面调用的本机接口
workers/
  worker-kit/        模型进程公共协议（启动握手、令牌、加载/卸载/运行）
  speech/            语音进程：mlx-speech
  analysis/          分析进程：pyannote、Qwen3-ForcedAligner、Demucs、Silero VAD
  llm/               LLM 进程说明（直接用 mlx-lm 自带服务）
  music/             音乐进程说明（直接用 ACE-Step 自带服务）
packaging/           内置 Python、签名、公证、扩展包构建脚本
scripts/             开发环境搭建、测试脚本
docs/                开发方案、架构说明
```

## 开发

### 准备

- Apple Silicon Mac（M1 或更新），macOS 14 或更新
- [uv](https://docs.astral.sh/uv/)、Node.js 22+、[pnpm](https://pnpm.io/)、[Rust](https://rustup.rs/)
- ffmpeg：`brew install ffmpeg`

### 运行

```bash
scripts/dev_setup.sh            # 建立基础环境 .dev/base-venv（引擎 + mlx-speech + mlx-lm）
scripts/dev.sh                  # 启动开发版 App

# 可选：扩展包
scripts/dev_setup_analysis.sh   # 分析环境（说话人区分、时间戳、人声分离）
scripts/dev_install_acestep.sh  # 音乐环境（ACE-Step 1.5）
```

模型在第一次使用时从 Hugging Face 下载到 `~/Library/Application Support/dev.shengjing.desktop/models`。
国内网络可以在设置里填写镜像站作为下载源。

### 测试

```bash
scripts/test_all.sh
```

这些测试不需要 Mac 和模型权重（用假模型进程代替），Linux 上也能跑；CI 同时在 macOS 上编译外壳、
检查 mlx-speech 能否安装导入。

## 打包发布

见 [packaging/README.md](packaging/README.md)。要点：需要苹果开发者账号签名和公证；
App 内置独立的 Python 运行环境；分析包和音乐包作为扩展包按需下载。

## 在线 API

在设置页填写对应服务商的 API Key（保存在 macOS 钥匙串里）后，它的模型就会出现在各页面的模型选择里。
使用在线 API 时，文字和音频会上传到服务商，并按服务商的价格计费；服务条款以各家官网为准。

| 服务商 | 语音合成 | 声音克隆 | 转写 | 区分说话人 | 音效 | 音乐 | 多人对话 | 接口文档 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| OpenAI 兼容接口 | ✅（可用文字描述语气） | — | ✅ | — | — | — | — | [文档](https://developers.openai.com/api/reference/resources/audio) |
| SenseAudio（商汤） | ✅ | ✅ 自动克隆一次后复用 | ✅ | ✅ | ✅ 1–10 秒 | ✅ | ✅ 一次生成多角色对白，最多 3 个参考音色 | [文档](https://docs.senseaudio.cn) |
| Fish Audio | ✅（支持行内情绪标签，可选 drama-3） | ✅ 每次直接带参考录音 | ✅ | — | — | — | — | [文档](https://docs.fish.audio) |
| ElevenLabs | ✅（eleven_v3 音频标签） | ✅ 自动克隆一次后复用 | ✅ | ✅ | ✅ 0.5–30 秒 | ✅ | — | [文档](https://elevenlabs.io/docs/api-reference) |

- **OpenAI 兼容接口**可以改接口地址，接入其他兼容 `/v1/audio/speech`、`/v1/audio/transcriptions` 的服务。
- **LLM** 另外在设置的“LLM”一栏配置，支持任意 OpenAI 兼容的对话接口（SenseAudio、DeepSeek 等也兼容）。
- 每家的默认模型和默认声音都可以在设置里改，留空则使用表格里代码默认值（见 `engine/src/shengjing_engine/providers/`）。
- 克隆音色的对应关系（哪段参考录音对应服务商的哪个音色 ID）记在本地数据库里，同一段录音只克隆一次。
- 这些接口按 2026 年 10 月的官方文档实现，自动化测试用模拟服务器检查请求格式，**还没有用真实 API Key 调用过**。

## 使用的开源框架与模型

模型权重**不随 App 分发**，用户第一次使用某个模型时从其官方仓库下载。
下表的许可证以各模型卡标注为准（2026 年 10 月核对），使用前请再次查看原文。
代码里的模型清单在 `engine/src/shengjing_engine/core/catalog.py`，界面的“关于与许可”页面会显示同样的信息。

### 应用框架

| 名称 | 用途 | 许可证 | 链接 |
| --- | --- | --- | --- |
| Tauri 2 | macOS App 外壳 | MIT 或 Apache-2.0 | https://github.com/tauri-apps/tauri |
| tauri-plugin-dialog | 文件选择对话框 | MIT 或 Apache-2.0 | https://github.com/tauri-apps/plugins-workspace |
| serde / serde_json | Rust 序列化 | MIT 或 Apache-2.0 | https://github.com/serde-rs/serde |
| React 19 | 界面 | MIT | https://github.com/facebook/react |
| Vite | 前端构建 | MIT | https://github.com/vitejs/vite |
| TypeScript | 前端语言 | Apache-2.0 | https://github.com/microsoft/TypeScript |

### 引擎与模型运行框架

| 名称 | 用途 | 许可证 | 链接 |
| --- | --- | --- | --- |
| CPython 3.13 / 3.12 | 内置运行环境 | PSF-2.0 | https://www.python.org |
| python-build-standalone | 可搬移的 Python 发行版（内含 OpenSSL、SQLite、zlib 等，各自许可证随发行版附带） | 构建脚本 MPL-2.0 | https://github.com/astral-sh/python-build-standalone |
| uv | Python 环境和依赖管理 | MIT 或 Apache-2.0 | https://github.com/astral-sh/uv |
| MLX | Apple Silicon 上的机器学习框架 | MIT | https://github.com/ml-explore/mlx |
| **mlx-speech** | TTS、ASR 推理（语音进程的核心） | MIT | https://github.com/appautomaton/mlx-speech |
| mlx-lm | 本地 LLM 服务 | MIT | https://github.com/ml-explore/mlx-lm |
| FastAPI | 引擎和模型进程的接口 | MIT | https://github.com/fastapi/fastapi |
| Uvicorn | ASGI 服务器 | BSD-3-Clause | https://github.com/encode/uvicorn |
| websockets | 任务进度推送（WebSocket） | BSD-3-Clause | https://github.com/python-websockets/websockets |
| Pydantic | 数据校验（剧本格式等） | MIT | https://github.com/pydantic/pydantic |
| HTTPX | HTTP 客户端 | BSD-3-Clause | https://github.com/encode/httpx |
| NumPy | 混音和音频处理 | BSD-3-Clause | https://github.com/numpy/numpy |
| keyring | 把密钥存进 macOS 钥匙串 | MIT | https://github.com/jaraco/keyring |
| msgpack | Fish Audio 克隆请求的编码格式 | Apache-2.0 | https://github.com/msgpack/msgpack-python |
| FFmpeg | 音视频编解码、响度标准化 | LGPL-2.1+（必须使用 LGPL 构建） | https://ffmpeg.org |
| PyTorch | 分析扩展包的运行框架 | BSD-3-Clause | https://github.com/pytorch/pytorch |
| pyannote.audio 4 | 说话人区分 | MIT | https://github.com/pyannote/pyannote-audio |
| qwen-asr | Qwen3-ForcedAligner 推理 | Apache-2.0 | https://github.com/QwenLM/Qwen3-ASR |
| Demucs | 人声和伴奏分离 | MIT | https://github.com/adefossez/demucs |
| silero-vad | 人声检测 | MIT | https://github.com/snakers4/silero-vad |
| soundfile / libsndfile | 读取音频文件 | BSD-3-Clause / LGPL-2.1 | https://github.com/bastibe/python-soundfile |
| ACE-Step 1.5（代码） | 音乐生成服务 | MIT | https://github.com/ace-step/ACE-Step-1.5 |
| pytest、pytest-asyncio、Ruff | 测试和代码检查（仅开发） | MIT、Apache-2.0、MIT | — |

### 语音模型（语音进程，通过 mlx-speech 运行）

商用一栏：✅ 许可证允许商用（可能有署名等义务）；⚠️ 有条件商用；❌ 非商用或需另获授权。
内存是峰值估算，标“实测”的来自 mlx-speech 文档，其余为“权重大小 × 1.3 + 1GB”，第 0 阶段实测后更新。

| 模型 | 用途 | 权重许可证 | 商用 | 内存 | 链接 |
| --- | --- | --- | --- | --- | --- |
| Fish S2 Pro | 朗读、克隆、行内情绪标签（个人版默认） | Fish Audio Research License | ❌ | ~9 GB | [MLX 权重](https://huggingface.co/appautomaton/fishaudio-s2-pro-8bit-mlx) |
| Fish Audio Drama 3 | 最多 16 角色对话、语气描述、指定时长 | Fish Audio Research License | ❌ | 46 GB（实测） | [官方仓库](https://huggingface.co/fishaudio/drama-3)（需自行转换） |
| dots.tts SOAR / MeanFlow | 朗读、克隆、流式输出（商用版默认） | Apache-2.0 | ✅ | 7.0 / 7.2 GB（实测） | [MLX 权重](https://huggingface.co/appautomaton/dots-tts-mlx) |
| FireRedTTS3 Base | 多语种克隆 | Apache-2.0 | ✅ | ~8.5 GB | [MLX 权重](https://huggingface.co/appautomaton/fireredtts3-mlx) |
| OpenMOSS TTS Local | 朗读、克隆 | Apache-2.0 | ✅ | ~7.5 GB | [MLX 权重](https://huggingface.co/appautomaton/openmoss-tts-local-mlx) |
| MOSS-TTSD v1.0 | 多人对话（默认对话模型） | Apache-2.0 | ✅ | ~14 GB | [MLX 权重](https://huggingface.co/appautomaton/openmoss-ttsd-mlx) |
| MOSS Audio Tokenizer | MOSS 系列共用的音频编解码器 | Apache-2.0 | ✅ | 含在上面 | [MLX 权重](https://huggingface.co/appautomaton/openmoss-audio-tokenizer-mlx) |
| LongCat AudioDiT 3.5B | 扩散式朗读、克隆 | MIT | ✅ | ~7 GB | [MLX 权重](https://huggingface.co/appautomaton/longcat-audiodit-3.5b-8bit-mlx) |
| VibeVoice Large | 长篇多人对话 | Apache-2.0（MLX 权重卡） | ✅ | ~13 GB | [MLX 权重](https://huggingface.co/appautomaton/vibevoice-mlx) |
| DramaBox | 48 kHz 立体声朗读 | LTX-2 Community License | ⚠️ | ~19 GB | [MLX 权重](https://huggingface.co/appautomaton/dramabox-tts-3.3b-bf16-mlx) |
| Gemma 3 12B（DramaBox 的文本编码器） | DramaBox 自动下载 | Gemma Terms of Use | ⚠️ | 含在上面 | [MLX 权重](https://huggingface.co/appautomaton/gemma-3-12b-it-backbone-4bit-mlx) |
| Breeze TTS 2 | 用文字描述造声音、指令控制语气 | BreezeBlue Research and Non-Commercial License | ❌ | 9.2 GB（实测） | [官方仓库](https://huggingface.co/BreezeBlue/Breeze-TTS-2)（需自行转换） |
| Step-Audio-EditX | 录音编辑：情绪、风格、语速、降噪等 | Apache-2.0 | ✅ | ~6.5 GB | [MLX 权重](https://huggingface.co/appautomaton/step-audio-editx-8bit-mlx) |
| MOSS-SoundEffect | 文字生成音效 | Apache-2.0 | ✅ | ~9 GB | [MLX 权重](https://huggingface.co/appautomaton/openmoss-sound-effect-mlx) |
| Qwen3-ASR 1.7B | 中文、英文、中英混合转写（默认） | Apache-2.0 | ✅ | ~4 GB | [MLX 权重](https://huggingface.co/appautomaton/qwen3-asr-1.7b-int8-mlx) |
| Cohere Transcribe | 长音频快速转写 | Apache-2.0 | ✅ | ~4 GB | [MLX 权重](https://huggingface.co/appautomaton/cohere-asr-mlx) |
| IBM Granite Speech 4.0 1B | 英法德西葡日转写（不支持中文） | Apache-2.0 | ✅ | ~4.5 GB | [MLX 权重](https://huggingface.co/appautomaton/granite-4.0-1b-speech-int8-mlx) |
| Confucius4-R2T2 | 实时转写，30 种语言（个人版默认） | NetEase Youdao Model Use License | ⚠️ | ~6 GB | [MLX 权重](https://huggingface.co/appautomaton/confucius4-r2t2-bf16-mlx) |
| NVIDIA Nemotron 3.5 ASR Streaming | 实时转写（商用版默认） | OpenMDW-1.1 | ✅ | ~2 GB | [MLX 权重](https://huggingface.co/appautomaton/nemotron-3.5-asr-streaming-0.6b-int8-mlx) |

以上 MLX 权重均由 mlx-speech 作者转换发布，原始模型分别来自 Fish Audio、dots-studio、FireRedTeam、
OpenMOSS、美团 LongCat、微软、Resemble AI、Google、BreezeBlue、阶跃星辰、Qwen、Cohere、IBM、
网易有道、NVIDIA。

### 本地 LLM（LLM 进程，通过 mlx-lm 运行）

| 模型 | 用途 | 许可证 | 商用 | 内存 | 链接 |
| --- | --- | --- | --- | --- | --- |
| Qwen3 4B（4bit） | 16GB 机器的默认 LLM | Apache-2.0 | ✅ | ~4 GB | https://huggingface.co/mlx-community/Qwen3-4B-4bit |
| Qwen3.5 9B（4bit） | 32GB 机器的默认 LLM | Apache-2.0 | ✅ | ~8 GB | https://huggingface.co/mlx-community/Qwen3.5-9B-4bit |
| Qwen3.6 35B-A3B（4bit） | 64GB 以上机器的默认 LLM | Apache-2.0 | ✅ | ~24 GB | https://huggingface.co/mlx-community/Qwen3.6-35B-A3B-4bit |

也可以在设置里改用任意 OpenAI 兼容的云端接口，此时适用该服务自己的条款。

### 分析扩展包（分析进程，通过 PyTorch 运行）

| 模型 | 用途 | 许可证 | 商用 | 链接 |
| --- | --- | --- | --- | --- |
| pyannote speaker-diarization-community-1 | 离线说话人区分 | CC-BY-4.0（需署名；下载前需在 Hugging Face 同意使用条款） | ✅ | https://huggingface.co/pyannote/speaker-diarization-community-1 |
| NVIDIA Streaming Sortformer 4spk v2 | 实时说话人区分（最多 4 人，第 4 阶段接入） | CC-BY-4.0（v1 是 CC-BY-NC，不要混用） | ✅ | https://huggingface.co/nvidia/diar_streaming_sortformer_4spk-v2 |
| Qwen3-ForcedAligner 0.6B | 词级时间戳 | Apache-2.0 | ✅ | https://huggingface.co/Qwen/Qwen3-ForcedAligner-0.6B |
| HT-Demucs | 人声和伴奏分离 | MIT | ✅ | https://github.com/adefossez/demucs |
| Silero VAD | 人声检测 | MIT | ✅ | https://github.com/snakers4/silero-vad |

### 音乐扩展包（音乐进程）

| 模型 | 用途 | 许可证 | 商用 | 链接 |
| --- | --- | --- | --- | --- |
| ACE-Step 1.5 | 带人声的歌曲和纯配乐，10 秒到 10 分钟 | MIT（模型卡写明生成的音乐可商用） | ✅ | https://huggingface.co/ACE-Step/Ace-Step1.5 |

### 评估过、暂未接入

| 模型 | 原因 | 许可证 |
| --- | --- | --- |
| Google Magenta RealTime 2 | 实时器乐生成，计划用于同传/直播背景音乐 | 代码 Apache-2.0，权重 CC-BY-4.0 |
| HeartMuLa 3B | 歌词控制好，但官方未提 Mac 支持 | Apache-2.0 |
| LeVo 2（腾讯 SongGeneration） | 许可证说法不一致，暂不使用 | 待核实 |
| YuE | 权重非商用，且需要 24GB NVIDIA 显卡 | 权重 CC-BY-NC-4.0 |
| RE-USE / SEMamba 降噪 | DramaBox 的可选降噪，默认关闭，本项目不启用 | NVIDIA 非商用许可 |

## 许可证注意事项

- **Fish S2 Pro 和 Drama 3 不能商用。** Fish Audio Research License 只允许研究和非商用；
  任何商用（包括做成在线服务）都必须另外取得 Fish Audio 的书面授权，没有营收门槛。
  使用时需附许可证并标注 “Built with Fish Audio”（App 的“关于与许可”页面会显示）。
  商用版默认改用 dots.tts（Apache-2.0）。
- **Breeze TTS 2 不能商用**，用它生成的音频同样受限。商用版没有“用文字造声音”的替代模型。
- **DramaBox**：LTX-2 Community License 要求年收入 1000 万美元以上的实体付费授权，
  并禁止未经同意模仿真人、要求标注机器生成内容；文本编码器另受 Gemma 使用条款约束。
- **Confucius4-R2T2**：大规模商用需要网易有道另行授权，商用版默认改用 Nemotron。
- **CC-BY-4.0 模型**（pyannote、Sortformer v2）：分发或展示结果时需要署名。
- **OpenMDW-1.1**（Nemotron）：再分发时保留许可证副本。
- **VibeVoice**：MLX 权重卡标注 Apache-2.0，上游为微软 VibeVoice，商用前请复核上游的使用声明。
- **FFmpeg**：App 内置的 ffmpeg 必须是 LGPL 构建（不含 `--enable-gpl` 组件），否则整个 App 要遵守 GPL。
- 以上是工程上的整理，不构成法律意见；商用发布前请让法务逐条确认原文。

## 合规

- **声音克隆必须取得授权**：添加音色时必须勾选授权确认，引擎会拒绝没有确认的请求，并记录确认时间。
- **AI 生成内容标识**：在中国境内发布时，需遵守《人工智能生成合成内容标识办法》等规定，
  对 AI 生成的音频添加显式和隐式标识（自动标识在第 5 阶段实现）。
- **数据去向**：只用本地模型时，所有音频、文字和模型推理都在本机完成；
  选择在线 API 或云端 LLM 时，相应的文字、音频和参考录音会上传给你配置的服务商。
- **克隆到在线服务**：用在线服务克隆声音时，参考录音会上传并在服务商账号里生成音色，
  同样需要声音所有者的授权。

## 本项目的许可证

本仓库的代码使用 [MIT 许可证](LICENSE)，Copyright (c) 2026 baiyakun。

MIT 只覆盖本仓库的代码，不改变任何第三方组件的许可：

- **模型权重**不在仓库里，也不随 App 分发，用户使用时从各模型的官方仓库下载，
  各自的许可证照常适用。例如 Fish S2 Pro、Drama 3、Breeze TTS 2 仍然只能非商用，
  见上面的[模型清单](#使用的开源框架与模型)和[许可证注意事项](#许可证注意事项)。
- **依赖的框架和库**按各自的许可证使用（见[框架清单](#应用框架)）。
- **发布版内置的 FFmpeg** 是独立程序，通过命令行调用，适用其自身的 LGPL 许可；
  分发 App 时需要同时提供 FFmpeg 的许可证和对应源码获取方式。
- **在线 API** 适用各服务商自己的条款。
