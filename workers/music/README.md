# 音乐进程（扩展包）

音乐进程直接使用 [ACE-Step 1.5](https://github.com/ace-step/ACE-Step-1.5)（代码和权重均为 MIT，
模型卡写明生成的音乐可以商用）自带的 API 服务，不需要自己写代码。

ACE-Step 要求 Python 3.11–3.12，而 mlx-speech 要求 3.13 以上，所以它单独一个运行环境，
作为扩展包在用户第一次生成音乐时下载。

## 主引擎怎么启动它

```bash
ACESTEP_API_HOST=127.0.0.1 \
ACESTEP_API_PORT=<随机端口> \
ACESTEP_API_KEY=<引擎令牌> \
<音乐环境>/bin/python3 -m acestep.api_server
```

地址、端口和 API Key 都通过环境变量传入（见 ACE-Step 文档 `docs/en/API.md` 第 13 节）。
引擎令牌被用作 API Key，所以只有主引擎能调用它。

## 调用流程

1. `POST /release_task` 提交任务，得到 `task_id`
2. `POST /query_result` 轮询，`status` 为 1 成功、2 失败
3. `GET /v1/audio?path=...` 下载音频

客户端代码见 `engine/src/shengjing_engine/workflows/music.py`。

## 开发时安装

```bash
scripts/dev_install_acestep.sh   # 克隆 ACE-Step 并用 uv 建立 Python 3.12 环境
export SHENGJING_PY_MUSIC=$PWD/.dev/ACE-Step-1.5/.venv/bin/python
```

## 待验证（第 0 阶段）

- 在 Apple Silicon 上用 MLX 后端运行时的内存占用（目录里暂按 12GB 估算）
- 中文歌词的效果
- `thinking` 模式（5Hz LM 规划）对质量和速度的影响
