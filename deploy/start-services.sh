#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)

# 加载模型路径、端口和 API Key；同时检查 vLLM 可执行文件。
# shellcheck source=vllm/common.sh
source "$SCRIPT_DIR/vllm/common.sh"

require_value CHAT_API_KEY
require_value EMBEDDING_API_KEY

CHAT_PORT=${VLLM_CHAT_PORT:-8100}
EMBEDDING_PORT=${VLLM_EMBEDDING_PORT:-8101}
RAG_API_PORT=${RAG_API_PORT:-8001}
START_TIMEOUT=${SERVICE_START_TIMEOUT:-600}
RAG_PYTHON=${RAG_PYTHON:-"$REPO_ROOT/.venv/bin/python"}

command -v screen >/dev/null 2>&1 || {
    echo "找不到 screen，请先安装 screen。" >&2
    exit 2
}
command -v curl >/dev/null 2>&1 || {
    echo "找不到 curl，请先安装 curl。" >&2
    exit 2
}
if [[ ! -x "$RAG_PYTHON" ]]; then
    echo "找不到 FastAPI Python 环境：$RAG_PYTHON" >&2
    exit 2
fi
if [[ ! -f "$REPO_ROOT/.env" ]]; then
    echo "缺少 FastAPI 配置：$REPO_ROOT/.env" >&2
    exit 2
fi

mkdir -p "$REPO_ROOT/logs"
cd "$REPO_ROOT"

screen_exists() {
    local session=$1
    screen -ls 2>/dev/null | grep -Eq "[[:space:]][0-9]+\.${session}[[:space:]]"
}

start_screen() {
    local session=$1
    local log_file=$2
    shift 2

    if screen_exists "$session"; then
        echo "[$session] Screen 会话已存在，继续检查服务。"
        return
    fi

    printf '\n===== %s 启动 %s =====\n' "$session" "$(date '+%F %T')" >>"$log_file"
    screen -L -Logfile "$log_file" -dmS "$session" "$@"
    sleep 1
    if ! screen_exists "$session"; then
        echo "[$session] 启动后立即退出，最近日志如下：" >&2
        tail -n 40 "$log_file" >&2 || true
        exit 1
    fi
    echo "[$session] 已创建 Screen 会话。"
}

wait_for_http() {
    local label=$1
    local session=$2
    local log_file=$3
    shift 3
    local deadline=$((SECONDS + START_TIMEOUT))
    local next_report=$SECONDS

    while ((SECONDS < deadline)); do
        if curl --noproxy '*' --fail --silent --show-error --max-time 5 "$@" \
            >/dev/null 2>&1; then
            echo "[$session] $label 已就绪。"
            return
        fi
        if ! screen_exists "$session"; then
            echo "[$session] 等待 $label 时进程退出，最近日志如下：" >&2
            tail -n 40 "$log_file" >&2 || true
            exit 1
        fi
        if ((SECONDS >= next_report)); then
            echo "[$session] 正在等待 $label，最多等待 ${START_TIMEOUT} 秒……"
            next_report=$((SECONDS + 10))
        fi
        sleep 2
    done

    echo "[$session] 等待 $label 超时，最近日志如下：" >&2
    tail -n 40 "$log_file" >&2 || true
    exit 1
}

echo "[1/3] 启动 vLLM 生成服务（GPU ${VLLM_CHAT_GPU:-0}，端口 $CHAT_PORT）"
start_screen \
    vllm-chat \
    "$REPO_ROOT/logs/vllm-chat.log" \
    "$SCRIPT_DIR/vllm/start-generation.sh"
wait_for_http \
    "生成模型接口" \
    vllm-chat \
    "$REPO_ROOT/logs/vllm-chat.log" \
    -H "Authorization: Bearer $CHAT_API_KEY" \
    "http://127.0.0.1:${CHAT_PORT}/v1/models"

echo "[2/3] 启动 vLLM 向量服务（GPU ${VLLM_EMBEDDING_GPU:-1}，端口 $EMBEDDING_PORT）"
start_screen \
    vllm-embedding \
    "$REPO_ROOT/logs/vllm-embedding.log" \
    "$SCRIPT_DIR/vllm/start-embedding.sh"
wait_for_http \
    "向量模型接口" \
    vllm-embedding \
    "$REPO_ROOT/logs/vllm-embedding.log" \
    -H "Authorization: Bearer $EMBEDDING_API_KEY" \
    "http://127.0.0.1:${EMBEDDING_PORT}/v1/models"

echo "正在执行两个 vLLM 服务的实际生成与向量冒烟检查……"
"$SCRIPT_DIR/vllm/check-services.sh"

echo "[3/3] 启动 FastAPI RAG 服务（端口 $RAG_API_PORT）"
start_screen \
    rag-api \
    "$REPO_ROOT/logs/rag-api.log" \
    "$RAG_PYTHON" -m uvicorn app.main:app \
    --host 0.0.0.0 \
    --port "$RAG_API_PORT" \
    --workers 1
wait_for_http \
    "FastAPI 健康检查" \
    rag-api \
    "$REPO_ROOT/logs/rag-api.log" \
    "http://127.0.0.1:${RAG_API_PORT}/api/health"

echo
echo "三个服务均已启动："
echo "  生成模型：http://127.0.0.1:${CHAT_PORT}/v1"
echo "  向量模型：http://127.0.0.1:${EMBEDDING_PORT}/v1"
echo "  RAG API：http://127.0.0.1:${RAG_API_PORT}/docs"
echo "查看会话：screen -ls"
echo "停止服务：deploy/stop-services.sh"
