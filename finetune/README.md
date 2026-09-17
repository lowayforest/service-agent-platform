# 本地微调冒烟测试

这里保存可公开的最小训练样例。训练模型缓存、LoRA 权重和私有业务数据不会提交 Git。

本机使用 Apple MLX 和约 0.8B 的 4-bit 千问模型，只验证 QLoRA 训练流程；正式效果评测仍应在双 RTX 5090 服务器上使用经过业务审核的数据进行。

## 已验证环境

- 设备：Apple M5，16GB 统一内存。
- Python：3.14.7。
- MLX：0.32.2。
- MLX-LM：0.31.3。
- 模型：`mlx-community/Qwen3.5-0.8B-4bit`。
- 数据：12 条训练、2 条验证、2 条测试样例。

该模型是社区转换的 Apple MLX 量化版本，基于官方 `Qwen/Qwen3.5-0.8B`。0.8B 只用于原型和流程验证，不能代替项目使用的 9B 或服务器 27B。

## 安装

训练依赖与 RAG 应用隔离：

```bash
python3 -m venv .venv-train
source .venv-train/bin/activate
python -m pip install --upgrade pip
python -m pip install -r finetune/requirements.txt
```

Hugging Face 模型会下载到用户缓存，不进入项目目录。

## 训练

```bash
python -m mlx_lm lora \
  --model mlx-community/Qwen3.5-0.8B-4bit \
  --train \
  --test \
  --data finetune/data/smoke \
  --fine-tune-type lora \
  --mask-prompt \
  --num-layers 8 \
  --batch-size 1 \
  --iters 30 \
  --val-batches -1 \
  --test-batches -1 \
  --learning-rate 2e-5 \
  --steps-per-report 5 \
  --steps-per-eval 10 \
  --adapter-path finetune/outputs/qwen3.5-0.8b-smoke \
  --save-every 10 \
  --max-seq-length 512 \
  --seed 42
```

模型本身是 4-bit，MLX-LM 会以 QLoRA 方式训练 LoRA 适配参数。

## 实测结果

| 指标 | 结果 |
| --- | ---: |
| 可训练参数 | 1.804M / 752.392M（0.240%） |
| 第 5 步训练损失 | 2.185 |
| 第 30 步训练损失 | 0.151 |
| 第 10 步验证损失 | 1.875 |
| 第 20 步验证损失 | 1.644 |
| 第 30 步验证损失 | 1.748 |
| 测试损失 / 困惑度 | 1.151 / 3.163 |
| 峰值统一内存 | 2.588GB |
| 训练速度 | 约 67～72 token/s（稳定阶段） |

验证损失在第 20 步后回升，说明这组极小数据已经开始过拟合。30 步结果只证明训练链路可用，不能据此判断真实业务质量。

同一条未参与训练的问题：

```text
检索证据：[S1] 历史通告不得作为当前管制状态，当前状态必须查询权威接口。
问题：去年的管制通告能证明今天仍在管制吗？
```

基础模型回答较长；微调模型回答为：

```text
不能。历史通告不得作为当前管制状态，必须查询权威接口。[S1]
```

这说明短训练已经改变回答风格，但尚不能证明事实准确率得到提升。

## 使用适配器推理

```bash
python -m mlx_lm generate \
  --model mlx-community/Qwen3.5-0.8B-4bit \
  --adapter-path finetune/outputs/qwen3.5-0.8b-smoke \
  --system-prompt '你是航道对外服务知识库助手。只依据检索证据回答，并引用来源。' \
  --prompt '检索证据：[S1] 历史资料不能作为实时数据。问题：可以把去年的水深当成今天的数据吗？' \
  --chat-template-config '{"enable_thinking":false}' \
  --max-tokens 100 \
  --temp 0
```

## 重要边界

- 样例数据来自项目公开说明，不包含 `profile/` 原始资料正文。
- 不要把整份法规直接复制成训练数据，应由业务人员审核问题、证据和标准回答。
- `finetune/outputs/` 已被 Git 忽略；适配器也需要按模型资产进行权限管理。
- 服务器正式训练前，应先建立独立测试集，并比较原模型、微调模型和更大原模型。
- 本次适配器只能与相同的 MLX 基础模型组合使用，不能直接交给 Ollama。

参考：[Qwen3.5-0.8B 官方模型卡](https://huggingface.co/Qwen/Qwen3.5-0.8B)、[MLX-LM LoRA 文档](https://github.com/ml-explore/mlx-lm/blob/main/mlx_lm/LORA.md)。
