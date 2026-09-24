# 航道资料 OCR 部署与操作手册

> 适用版本：本地最小 RAG MVP 0.1.0
> 更新日期：2026-09-21
> 适用对象：开发人员、资料整理人员、服务器管理员

## 1. 当前结论

项目已经接入两种 OCR 后端：

| 后端 | 参数 | 适用环境 | 输出特点 |
| --- | --- | --- | --- |
| PP-OCRv5 mobile | `--ocr-backend paddleocr` | 16 GB Mac、CPU 快速验证 | 提取文字和页码，复杂表格会失去行列关系 |
| PaddleOCR-VL 1.6 | `--ocr-backend paddleocr-vl` | RTX 5090 等 GPU | 更好地恢复标题、表格、公式和阅读顺序 |

2026-09-17 已在 Apple M5、16 GB 内存的 Mac 上完成真实资料验证：一份 3 页扫描 PDF
成功生成 Markdown，包含 3 个页级片段和 2,410 个正文字符。抽查发现标题、河段名称和
多数数字可以识别，但轻量后端对复杂表格只能按行输出，并存在少量字符误识别。

同一台 Mac 已成功下载并加载 PaddleOCR-VL 1.6，但推理进程占用约 12 GB 内存，导致系统
重度换页、交换区耗尽且电脑卡死，不能作为可用的本机方案。项目现在会拒绝
`--ocr-backend paddleocr-vl --ocr-device cpu`；在 PaddlePaddle 不是 CUDA 版或找不到
指定 GPU 时，也会在加载 VL 模型前报错。VL 后端只在双 RTX 5090 服务器验证。

2026-09-21 已在实验室双 RTX 5090 服务器完成 PaddleOCR-VL 1.6 实测。先导和批量样本共
10 份、141 页，自动检查确认 130 页含 OCR 内容，其余 11 页均为像素级纯白空白页；共提取
362,945 个字符、69 个 HTML 表格和 57 个图片引用，10 份全部自动通过。自动通过只说明
文件、页码覆盖和基础结构正常，关键数字和表格仍需人工验收。

## 2. 为什么使用独立环境

主 RAG 环境当前是 Python 3.14，PaddleOCR 官方硬件教程验证的是 Python 3.9～3.13。
OCR 还会安装 PaddlePaddle、OpenCV、PDF 渲染、表格识别等大量依赖。把它们放入
`.venv-ocr` 可以避免影响已经运行正常的 FastAPI、Ollama 和向量检索环境。

目录和模型缓存均不会提交 Git：

```text
.venv/                 主 RAG 环境
.venv-ocr/             OCR 独立环境
~/.paddlex/             PaddleOCR 模型缓存，位于用户目录
data/processed/         标准化 Markdown
data/manifests/         审计和处理台账
```

## 3. Mac 本机安装

安装并固定 Python 3.12：

```bash
brew install python@3.12
/opt/homebrew/bin/python3.12 -m venv .venv-ocr
```

安装 Apple Silicon CPU 版 PaddlePaddle，再安装项目和 OCR 依赖：

```bash
.venv-ocr/bin/python -m pip install --upgrade pip
.venv-ocr/bin/python -m pip install paddlepaddle==3.2.1 \
  -i https://www.paddlepaddle.org.cn/packages/stable/cpu/
.venv-ocr/bin/python -m pip install -r requirements-ocr.txt
```

验证环境：

```bash
.venv-ocr/bin/python -c \
  'import paddle, paddleocr; print(paddle.__version__, paddleocr.__version__, paddle.device.get_device())'
```

当前已验证输出应包含 PaddlePaddle `3.2.1`、PaddleOCR `3.7.0` 和设备 `cpu`。

## 4. 本机手动测试

不要第一次就处理整个 `profile/`。先选一份 1～5 页的扫描 PDF：

```bash
.venv-ocr/bin/python -m scripts.preprocess "profile/某份扫描资料.pdf" \
  --ocr-backend paddleocr \
  --ocr-device cpu \
  --output-dir data/processed-ocr-smoke \
  --manifest data/manifests/ocr-smoke.jsonl \
  --strict
```

成功时会看到：

```text
[ready] profile/某份扫描资料.pdf
```

检查台账和结果：

```bash
jq . data/manifests/ocr-smoke.jsonl
find data/processed-ocr-smoke -type f -name '*.md'
```

人工打开 Markdown，至少检查：

- 标题和正文是否存在。
- 页数是否与 PDF 一致。
- 航段名称、法规条款号、年份和关键数字是否正确。
- 表格的表头、行名和数字是否仍能对应。
- 是否出现大量乱码、漏页或无中生有的内容。

轻量后端通过只代表流程可用，不代表复杂表格达到入库质量。

## 5. DOCX 内嵌图片

预处理命令会先用 `python-docx` 提取正文和表格，再将 DOCX 压缩包中
`word/media/` 下的 PNG、JPG、BMP、TIFF 和 WebP 图片交给同一个 OCR 后端。

```bash
.venv-ocr/bin/python -m scripts.preprocess "profile/某份含图片资料.docx" \
  --ocr-backend paddleocr \
  --ocr-device cpu
```

EMF、WMF 等矢量图片暂不识别，会记录为 `partial_needs_image_ocr`，不会静默丢弃。
DOCX 内的徽标和装饰图片也可能被识别，因此入库前仍要人工抽查。

## 6. 双 RTX 5090 服务器方案

本项目服务器端推荐 **Python 3.12 + 两个独立 `.venv`**：`.venv` 运行 RAG API，
`.venv-ocr` 运行文档预处理。`.venv` 隔离的是 Python 包，不会安装 Python 解释器、
NVIDIA 驱动或 Ollama；无需为本项目额外安装 Conda。若服务器没有 Python 3.12，
先请管理员提供 Python 3.12 和 `venv` 支持，不要替换系统自带的 `python3`。
主应用的分步部署见 [部署手册](部署手册.md) 第 6 节。

### 6.1 已验证环境

实验室服务器是两张 RTX 5090，属于 NVIDIA Blackwell。原驱动 570.133.07 只显示 CUDA
12.8，未达到本项目采用的 PaddleOCR Blackwell 路线要求；经管理员升级并重启后，当前实测
环境如下：

| 项目 | 已验证值 |
| --- | --- |
| GPU | 2 × NVIDIA GeForce RTX 5090，每张 32,607 MiB |
| NVIDIA 驱动 | 580.126.09 |
| GPU Compute Capability | 12.0 |
| Paddle Driver API / Runtime API | 13.0 / 12.9 |
| Python | 3.12.14 |
| PaddlePaddle GPU | 3.2.1，CUDA 构建为 `True` |
| PaddleOCR | 3.7.0 |
| PaddleOCR-VL 流水线 | v1.6 |
| 实测设备 | `gpu:1` |

驱动升级是整台服务器变更；在其他服务器复现时仍需先征得管理员同意、确认没有其他 GPU
任务，再升级和重启。`nvidia-smi` 显示的 CUDA 版本是驱动可支持的最高 CUDA 版本，并不
表示已经安装 CUDA Toolkit，也不能通过只安装 Python 包跳过驱动要求。

### 6.2 项目推荐的 `.venv-ocr` 路线

确认管理员已经完成驱动升级，并且 `nvidia-smi` 显示支持 CUDA 12.9 或更高版本后，
在**服务器项目目录**执行；以下命令不应在 Mac 上执行。官方优先推荐 Docker 以减少
兼容性问题，但本服务器尚无 Docker，因此先采用官方同样支持的手动安装路线；
这一路线仍需在 5090 上实测，不能把安装成功当作推理成功：

```bash
python3.12 --version
nvidia-smi
python3.12 -m venv .venv-ocr
.venv-ocr/bin/python -m pip install --upgrade pip
.venv-ocr/bin/python -m pip install paddlepaddle-gpu==3.2.1 \
  -i https://www.paddlepaddle.org.cn/packages/stable/cu129/
.venv-ocr/bin/python -m pip install -r requirements-ocr.txt
```

先只检查环境，不加载 OCR 模型：

```bash
.venv-ocr/bin/python -c 'import paddle; print("Paddle", paddle.__version__, "CUDA 构建", paddle.device.is_compiled_with_cuda(), "可见 GPU", paddle.device.cuda.device_count())'
```

必须看到 `CUDA 构建 True` 和至少 `可见 GPU 1`；否则停止，不要通过改成 CPU
继续尝试。首次用**一份已经获准放到实验室服务器、约 1～3 页的扫描 PDF**测试：

```bash
.venv-ocr/bin/python -m scripts.preprocess "/受控资料目录/三页扫描样本.pdf" \
  --ocr-backend paddleocr-vl \
  --ocr-device gpu:1 \
  --output-dir data/processed-ocr-vl \
  --manifest data/manifests/ocr-vl-results.jsonl \
  --strict
```

`--pdf-page-limit` 只限制“分类审计”检查的页数，**不会限制 OCR 实际处理页数**。
因此首测必须选择本来就只有少量页的文件，不能把几十页 PDF 直接交给命令。
成功后检查 Markdown 的页数、表格、数字和来源，再考虑扩大样本。本次实测使用 `gpu:1`，
便于将另一张卡保留给 RAG/模型服务；这只是设备分工，不是双卡并行 OCR。另开终端使用
`nvidia-smi` 观察显存和进程，不要同时在同一张卡启动多个 VL OCR 批次。

模型完整下载到 `~/.paddlex/official_models/PaddleOCR-VL-1.6` 后，可在网络不稳定时设置：

```bash
export PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK=True
```

该变量只跳过模型源连通性检查，不会补全损坏或未下载完成的模型。首次下载尚未完成时不能
依赖它；批次结束后可执行 `unset PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK` 恢复当前终端。

### 6.3 官方 Docker 备选路线

如果管理员已经提供 Docker 和 NVIDIA Container Toolkit，也可先验证官方 Blackwell 镜像：

```bash
docker run --rm -it \
  --gpus all \
  --network host \
  --user root \
  ccr-2vdh3abv-pub.cnc.bj.baidubce.com/paddlepaddle/paddleocr-vl:latest-nvidia-gpu-sm120 \
  /bin/bash
```

这是官方运行环境的备选验证方式，**上面的命令不会自动挂载项目或资料目录**。
本项目首轮优先使用 6.2 节 `.venv-ocr` 路线；需要容器化时再由管理员设计受控目录挂载。

## 7. 目标 Atlas 服务器的处理策略

PaddleOCR 官方昇腾教程目前明确验证的是 Ascend 910B，并未确认 Atlas 300I Duo。因此本项目
不把“在目标服务器上直接跑 OCR”设为第一阶段硬要求。

推荐流程是：

```text
原始资料
  → 实验室 RTX 5090 执行 PaddleOCR-VL
  → 人工抽查和修订 Markdown
  → 构建可重复的知识库索引
  → 将代码、合规的 Markdown、索引和模型部署到目标服务器
```

这样可以把硬件差异限制在模型推理层。目标服务器只需要读取已经标准化的文本，不必重新
解析全部扫描件。若目标方要求资料不得离开现场，则再单独验证 Atlas 300I Duo 或使用目标
服务器 CPU OCR，不能默认套用 Ascend 910B 镜像。

## 8. 批量验收标准

正式批量处理前建立 10～20 份“金标准”样本，至少覆盖法规正文、复杂表格、扫描歪斜、
印章、低清图片和含图 DOCX。建议记录：

| 指标 | 初步要求 |
| --- | --- |
| 页覆盖率 | 100%，不允许静默漏页 |
| 标题和章节顺序 | 抽查样本全部正确 |
| 关键数字准确率 | 航道尺度、水深、日期等逐项核对 |
| 表格结构 | 行列对应关系可供检索和回答 |
| 来源追溯 | 每个输出片段保留原文件和页码 |
| 失败可见性 | 失败文件必须进入台账，不能当作成功入库 |

只有金标准样本通过后，才对 71 份疑似扫描 PDF 和 8 份混合 PDF 执行批量 OCR。

### 8.1 自动生成验收报告

OCR 完成后，用同一个 `.venv-ocr` 自动核对台账状态、原 PDF 页数、OCR 页码、重复或越界
页码、乱码、表格和图片引用。没有 OCR 输出的页面只有在低分辨率渲染结果为完全纯白时才会
被自动认定为空白；模糊、浅色或无法确认的页面会令严格检查失败：

```bash
.venv-ocr/bin/python -m scripts.validate_ocr_batch \
  /home/shuncs/rag-data/ocr-batch-10 \
  --manifest data/manifests/ocr-vl-batch-10.jsonl \
  --report data/manifests/ocr-vl-batch-10-validation.md \
  --strict
```

如果先导和批量样本位于不同目录、使用不同台账，可以重复传入目录和 `--manifest`：

```bash
.venv-ocr/bin/python -m scripts.validate_ocr_batch \
  /home/shuncs/rag-data/pilot-ocr \
  /home/shuncs/rag-data/ocr-batch-10 \
  --manifest data/manifests/ocr-vl-pilot.jsonl \
  --manifest data/manifests/ocr-vl-batch-10.jsonl \
  --report data/manifests/ocr-vl-validation-10.md \
  --strict
```

命令同时生成同名 JSON 报告。报告位于被 Git 忽略的 `data/manifests/`，因为其中可能包含
原始文件名和本地路径。当前代码优先采用 Paddle 返回的零基 `page_index` 还原原 PDF 页码，
即使某个 Paddle 版本不再返回空白页对象，也不会用结果序号错误替代原页码。

当输入是包含原生文本 PDF、OCR PDF 和重复副本的完整资料目录时，命令增加 `--ocr-only`。
该开关只选择台账中状态为 `ready` 且解析器名称包含 `paddleocr` 的 PDF，避免将原生文本
PDF 和 `duplicate_skipped` 副本错误计入 OCR 验收。

本次服务器合并验收结果为：10/10 自动通过，141 个原始页面 = 130 个内容页面 + 11 个
纯白空白页面。下一步仍需按照上表逐份人工核对，而不是立即替换正式索引。

## 9. 官方参考

- [PaddleOCR 通用 OCR 使用教程](https://paddlepaddle.github.io/PaddleOCR/main/en/version3.x/pipeline_usage/OCR.html)
- [PaddleOCR-VL 使用教程](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL.en.md)
- [Apple Silicon 使用教程](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL-Apple-Silicon.en.md)
- [NVIDIA Blackwell 使用教程](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL-NVIDIA-Blackwell.en.md)
- [华为昇腾使用教程](https://github.com/PaddlePaddle/PaddleOCR/blob/main/docs/version3.x/pipeline_usage/PaddleOCR-VL-Huawei-Ascend-NPU.en.md)
