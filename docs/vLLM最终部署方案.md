# vLLM 最终部署方案

> 更新日期：2026-09-22
>
> 适用范围：实验室双 RTX 5090 验证服务器、长江航道局 Huanghe 2280 V2 目标服务器

## 1. 最终结论

最终应用不绑定 CUDA、昇腾或某一个推理引擎安装包，而是固定以下 HTTP 边界：

```text
用户
  ↓
FastAPI RAG :8000
  ├─ OpenAI Chat Completions :8100/v1   → 千问生成模型
  └─ OpenAI Embeddings       :8101/v1   → 千问向量模型
```

- 实验室双 RTX 5090 使用标准 vLLM 验证模型、接口、评测和并发。
- 目标 Atlas 300I Duo 使用 vLLM-Ascend，保持完全相同的应用配置和 OpenAI 接口。
- Ollama 只作为 Mac 开发和故障回退，不是目标服务器的生产依赖。
- 生成和向量服务使用两个进程、两个端口和独立资源配额，不能假设一个 vLLM 实例同时承载
  两个模型。

应用生产配置模板是仓库根目录的 `.env.vllm.example`。标准 vLLM 与 vLLM-Ascend 之间只
替换模型服务实现，不修改 RAG 业务代码。

## 2. 为什么目标机必须使用 vLLM-Ascend

目标服务器使用 4 张 Atlas 300I Duo（Device ID D500），不是 NVIDIA CUDA GPU。标准 vLLM
不能直接驱动这些卡，必须使用 vLLM-Ascend 的 310P 构建。

截至 2026-09-22，建议冻结并验证以下兼容组合，不要混装其他版本：

| 组件 | 目标基线 |
| --- | --- |
| vLLM / vLLM-Ascend | 0.23.0 |
| 官方容器 | `quay.io/ascend/vllm-ascend:v0.23.0-310p` |
| openEuler 容器 | `quay.io/ascend/vllm-ascend:v0.23.0-310p-openeuler` |
| CANN Toolkit + Ops | 9.1.0，310P 对应版本 |
| PyTorch / TorchNPU | 2.10.0 / 2.10.0.post4 |
| Python | 3.12 |
| Triton / Triton-Ascend | Atlas 300I Duo 不使用，不能误装 |

这是官方当前验证过的一组整体兼容栈。真正部署前仍需再次核对官方版本矩阵并记录镜像 digest，
不能只记录可变的 tag。

## 3. 模型选择

### 3.1 第一生产候选：Qwen3.5-9B FP16

目标机首先部署 `Qwen3.5-9B` FP16。vLLM-Ascend 已给出 Atlas 300I Duo 的专门部署和评测
说明，支持 TP=1 或 TP=2。项目先沿用已经通过 50 题基线的 9B 规模，可以把硬件迁移与模型
升级两个变量分开验证。

应用统一使用服务别名 `qwen3.5`，不把模型仓库路径写死在 FastAPI 配置里：

```text
CHAT_MODEL=qwen3.5
```

### 3.2 27B 是后续候选，不直接标为最终生产模型

支持矩阵已经列出 Atlas 300I Duo 对 Qwen3.5-27B 的支持，但现阶段目标机的 OS、驱动、固件、
CANN、逻辑 NPU 数量和卡间拓扑还没有现场采集，且 27B 专项部署资料主要以 A2/A3 为例。
因此必须先完成 9B 验收，再用同一评测集和压测方案验证 27B；未得到目标机实测数据前，不能
把 27B 写成既定生产参数。

### 3.3 向量模型

生产向量服务计划使用 `Qwen/Qwen3-Embedding-0.6B`，应用服务别名固定为
`qwen3-embedding`：

```text
EMBEDDING_MODEL=qwen3-embedding
```

vLLM-Ascend 当前将 Qwen3-Embedding 在 Atlas 300I Duo 上标为实验支持（FP16）。这意味着
它必须单独通过向量维度、批量顺序、中文召回和稳定性验收。如果现场验证不通过，应用层接口
不变，只替换 :8101 后面的 OpenAI 兼容向量服务。

从 Ollama 切换到 vLLM 向量服务后必须完整重建索引。项目生成的新索引会同时记录
`embedding_backend` 和 `embedding_model`，阻止旧 Ollama 索引被误用。

## 4. 应用配置

复制生产模板并设置受控密钥：

```bash
cp .env.vllm.example .env
```

核心配置如下：

```dotenv
CHAT_BACKEND=openai
CHAT_BASE_URL=http://127.0.0.1:8100/v1
CHAT_API_KEY=由运维生成的随机密钥
CHAT_MODEL=qwen3.5
CHAT_ENABLE_THINKING=false

EMBEDDING_BACKEND=openai
EMBEDDING_BASE_URL=http://127.0.0.1:8101/v1
EMBEDDING_API_KEY=由运维生成的随机密钥
EMBEDDING_MODEL=qwen3-embedding

INDEX_PATH=data/index-vllm.json
```

Qwen3.5 默认可能输出思考内容。客户端通过 `chat_template_kwargs.enable_thinking=false`
保持和当前 Ollama 基线一致，使引用检查、响应长度和延迟更可控。

### 4.1 两个模型端点的统一冒烟测试

无论后端是标准 vLLM 还是 vLLM-Ascend，启动后都执行同一组检查。先在当前终端设置实际
密钥；不要把真实值写进命令历史、文档或 Git：

```bash
printf 'CHAT_API_KEY: '; IFS= read -r -s CHAT_API_KEY; printf '\n'
printf 'EMBEDDING_API_KEY: '; IFS= read -r -s EMBEDDING_API_KEY; printf '\n'
export CHAT_API_KEY EMBEDDING_API_KEY
```

检查服务和生成模型：

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer $CHAT_API_KEY" \
  http://127.0.0.1:8100/v1/models | python3 -m json.tool

curl --fail --silent --show-error \
  -H "Authorization: Bearer $CHAT_API_KEY" \
  -H 'Content-Type: application/json' \
  http://127.0.0.1:8100/v1/chat/completions \
  -d '{"model":"qwen3.5","messages":[{"role":"user","content":"只回答：服务正常"}],"temperature":0,"max_tokens":20,"chat_template_kwargs":{"enable_thinking":false}}' \
  | python3 -m json.tool
```

检查向量数量与维度：

```bash
curl --fail --silent --show-error \
  -H "Authorization: Bearer $EMBEDDING_API_KEY" \
  -H 'Content-Type: application/json' \
  http://127.0.0.1:8101/v1/embeddings \
  -d '{"model":"qwen3-embedding","input":["长江航道公共服务"]}' \
  | python3 -c 'import json,sys; d=json.load(sys.stdin); print("向量数量:",len(d["data"]),"向量维度:",len(d["data"][0]["embedding"]))'
```

三条命令都成功后，填写 `.env`，用新向量端点建立独立索引并启动应用：

```bash
source .venv/bin/activate
python -m scripts.ingest README.md
uvicorn app.main:app --host 127.0.0.1 --port 8000
```

另开终端检查 `http://127.0.0.1:8000/api/health`。返回值中的两个 backend 都应为
`openai`，模型名应分别为 `qwen3.5` 和 `qwen3-embedding`，`indexed_chunks` 应大于 0。

## 5. 实验室双 RTX 5090 验证

标准 vLLM 应优先使用独立容器；若采用 Python 安装，则必须使用独立 `.venv-vllm`，不能
装进主应用 `.venv` 或 OCR 的 `.venv-ocr`。先用 9B 验证接口，再评估 27B。以下是起始
参数，不是未经压测即可发布的生产参数：

RTX 5090 属于 Blackwell。当前标准 vLLM 官方 Qwen3.5 recipe 对 Blackwell 推荐
`vllm/vllm-openai:cu130-nightly`；实验室若使用该镜像，必须在测试当天记录镜像 digest，
验证通过后按 digest 固定，不能在后续回归中继续跟随可变的 `nightly` 标签。若暂不使用
Docker，也必须在独立 `.venv-vllm` 中锁定完整 `pip freeze`，不得污染已经验证的 OCR 环境。

```bash
CUDA_VISIBLE_DEVICES=0 vllm serve Qwen/Qwen3.5-9B \
  --host 127.0.0.1 \
  --port 8100 \
  --api-key "$CHAT_API_KEY" \
  --served-model-name qwen3.5 \
  --tensor-parallel-size 1 \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.80 \
  --reasoning-parser qwen3 \
  --language-model-only
```

向量服务使用另一张卡；OCR 与向量服务不能在 GPU 1 上同时运行：

```bash
CUDA_VISIBLE_DEVICES=1 vllm serve Qwen/Qwen3-Embedding-0.6B \
  --host 127.0.0.1 \
  --port 8101 \
  --api-key "$EMBEDDING_API_KEY" \
  --served-model-name qwen3-embedding \
  --runner pooling \
  --max-model-len 8192 \
  --gpu-memory-utilization 0.50
```

双 5090 跨 NUMA 且没有 NVLink。27B 的 TP=2 性能必须实测，不能把两卡理解成一块连续
64 GB 显存。

仓库已经提供 `deploy/vllm/` 脚本，将生成模型固定在 GPU 0、向量模型固定在 GPU 1，并
提供统一冒烟检查。服务器拉取代码后按 [启动脚本说明](../deploy/vllm/README.md) 配置，避免
每次手工复制长命令。

## 6. 目标 Atlas 服务器部署门槛

### 6.1 先采集，后安装

目标机到手后先只读采集以下信息，不立即升级驱动或安装 CANN：

```bash
cat /etc/os-release
uname -a
lscpu
free -h
df -h
npu-smi info
ls -l /dev/davinci* /dev/davinci_manager /dev/devmm_svm /dev/hisi_hdc
cat /usr/local/Ascend/driver/version.info
docker version
```

必须确认：

- 实际 OS 是 Ubuntu 还是 openEuler，据此选择正确的 `-310p` 镜像；
- 4 张物理卡对应多少个 `/dev/davinciN` 逻辑设备；
- 驱动、固件与 CANN 9.1.0 容器要求是否兼容；
- Docker、模型盘、共享内存、10 Gb 网络、NTP 和日志盘是否可用；
- 设备健康状态以及 HCCL/PCIe 拓扑；
- 原始资料、模型权重、索引和日志允许存放的位置与权限。

缺少任何一项都不能照抄 Docker 命令。特别是 `/dev/davinci0-7` 只能根据现场枚举结果挂载，
不能由“4 张卡”直接推断。

### 6.2 容器路线

目标机优先使用官方预构建镜像，不在宿主机手工混装 PyTorch、TorchNPU 和 vLLM。Ubuntu
使用 `v0.23.0-310p`，openEuler 使用 `v0.23.0-310p-openeuler`。容器需要挂载现场确认的
设备节点、驱动库、模型只读目录和受控缓存目录。

Atlas 300I Duo 的模型启动参数至少要包含：

```text
--dtype float16
--mamba-ssm-cache-dtype float16
--additional-config '{"ascend_compilation_config":{"enable_npugraph_ex":false}}'
```

TP 模式受到硬件 event-id 资源约束，图捕获尺寸不能直接照搬 CUDA 配置。第一次先运行官方
Qwen3.5-9B 示例参数，再根据目标并发逐项调整 `max-model-len`、`max-num-seqs` 和显存比例。

## 7. 索引迁移

生产索引不能直接复用当前 Ollama 索引。操作顺序是：

1. 启动 :8101 向量服务并用 `/v1/embeddings` 验证向量数量与维度。
2. 使用相同的已验收 Markdown、切分大小 900、重叠 120 重建 `data/index-vllm.json`。
3. 运行固定 50 题及新增 OCR 业务题。
4. 核对来源、页码、表格数值和无答案拒答。
5. 只有通过验收后才让生产 API 使用新索引。

构建期间原 `data/index.json` 保持不变，可随时切回 Ollama 基线。

## 8. 最终验收与回滚

生产发布至少满足：

- 生成、向量和 FastAPI 三个服务都有独立健康检查、超时、日志和重启策略；
- 50 道基线题无无解释退化，新增 OCR 文档每份至少有一道可核验题；
- 单并发、2 并发、4 并发分别记录首字延迟、完整延迟、成功率和吞吐；
- NPU 显存、温度、功耗和错误码持续监控；
- vLLM 两个端口只监听本机或受控服务网段，不对用户直接开放；
- 镜像 digest、模型 revision、配置、索引清单和评测结果全部留档；
- 回滚只需恢复上一 Git 提交、`.env` 和对应索引，不依赖重新下载模型。

## 9. 官方依据

- [vLLM-Ascend 安装与版本矩阵](https://docs.vllm.ai/projects/ascend/en/v0.23.0/installation.html)
- [Atlas 300I Duo 的 Qwen3.5-2B/4B/9B 指南](https://docs.vllm.ai/projects/ascend/en/v0.23.0/tutorials/models/Qwen3.5-Dense.html)
- [vLLM-Ascend 模型支持矩阵](https://docs.vllm.ai/projects/ascend/en/main/user_guide/support_matrix/supported_models.html)
- [Atlas 300I Duo 限制](https://docs.vllm.ai/projects/ascend/en/v0.21.0rc/tutorials/hardwares/310p.html)
- [标准 vLLM 的 Qwen3.5 recipe](https://docs.vllm.ai/projects/recipes/en/stable/Qwen/Qwen3.5.html)
- [标准 vLLM OpenAI 兼容服务](https://docs.vllm.ai/en/latest/serving/openai_compatible_server.html)
