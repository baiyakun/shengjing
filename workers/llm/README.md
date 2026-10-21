# LLM 进程

LLM 进程不需要自己写代码，直接使用 [mlx-lm](https://github.com/ml-explore/mlx-lm)（MIT）
自带的 OpenAI 兼容服务。它和语音进程共用基础运行环境。

主引擎按需启动它，启动命令等价于：

```bash
python -m mlx_lm.server --model mlx-community/Qwen3-4B-4bit --host 127.0.0.1 --port <随机端口>
```

- 就绪检测：轮询 `GET /v1/models`
- 调用：`POST /v1/chat/completions`
- 换模型：主引擎会关闭进程，用新模型重新启动（单模型进程）
- mlx-lm 的服务没有鉴权，所以只监听 127.0.0.1

默认模型按机器内存选择（见 `engine/src/shengjing_engine/core/catalog.py`）：

| 内存 | 默认模型 | 许可证 |
| --- | --- | --- |
| 16GB | `mlx-community/Qwen3-4B-4bit` | Apache-2.0 |
| 32GB | `mlx-community/Qwen3.5-9B-4bit` | Apache-2.0 |
| 64GB 以上 | `mlx-community/Qwen3.6-35B-A3B-4bit` | Apache-2.0 |

在设置里也可以改用任意 OpenAI 兼容的云端接口，这时不会启动本地 LLM 进程。
