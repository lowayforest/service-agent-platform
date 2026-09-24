# 双 RTX 5090 vLLM 启动脚本

本目录用于实验室服务器验证。目标 Atlas 300I Duo 必须改用 vLLM-Ascend，不能执行这里的
CUDA 脚本。

## 准备配置

```bash
cp deploy/vllm/runtime.env.example deploy/vllm/runtime.env
chmod 600 deploy/vllm/runtime.env
```

填写两个本地模型目录和两个不同的随机 API key。`runtime.env`、模型权重和日志均被 Git
忽略。

## 下载模型

Ollama 的 GGUF 缓存不作为本次 vLLM/Atlas 验证权重。依赖安装完成后，先在
`.venv-vllm` 安装 ModelScope CLI，再顺序下载向量模型和生成模型：

```bash
uv pip install --python .venv-vllm/bin/python modelscope
deploy/vllm/download-models.sh
```

下载目录默认为仓库内被 Git 忽略的 `models/`。脚本可以重复执行；网络中断后再次执行用于
续传。ModelScope 上的两个仓库均来自 Qwen 官方组织。

## 启动

确认 OCR 和 Ollama 模型没有占用 GPU 后，推荐在仓库根目录执行统一启动脚本：

```bash
deploy/start-services.sh
```

脚本会依次启动并检查 `vllm-chat`、`vllm-embedding` 和 `rag-api` 三个 Screen 会话。只有
前一个服务通过健康检查后才会启动下一个服务；重复执行时会复用已经存在且健康的会话。
默认每个服务最多等待 600 秒，可临时通过 `SERVICE_START_TIMEOUT` 调整。

需要逐个手工启动 vLLM 时执行：

```bash
mkdir -p logs
screen -L -Logfile "$PWD/logs/vllm-chat.log" -dmS vllm-chat \
  deploy/vllm/start-generation.sh
screen -L -Logfile "$PWD/logs/vllm-embedding.log" -dmS vllm-embedding \
  deploy/vllm/start-embedding.sh
```

查看日志：

```bash
tail -f logs/vllm-chat.log
tail -f logs/vllm-embedding.log
```

两个日志都出现 `Application startup complete` 后运行：

```bash
deploy/vllm/check-services.sh
```

## 停止

推荐在仓库根目录执行统一停止脚本：

```bash
deploy/stop-services.sh
```

脚本按照 FastAPI、生成模型、向量模型的顺序停止三个 Screen 会话，并确认 `8001`、`8100`
和 `8101` 端口已经释放。它不会停止 Docker 前端容器，也不会强制终止不属于这些 Screen
会话的未知进程。

需要逐个手工停止 vLLM 时执行：

```bash
screen -S vllm-chat -X quit
screen -S vllm-embedding -X quit
```
