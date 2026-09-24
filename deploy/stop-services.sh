#!/usr/bin/env bash

set -uo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
REPO_ROOT=$(cd -- "$SCRIPT_DIR/.." && pwd)
RUNTIME_ENV="$SCRIPT_DIR/vllm/runtime.env"

# 停止操作即使在配置文件暂时缺失时也应可用，因此这里提供端口默认值。
if [[ -f "$RUNTIME_ENV" ]]; then
    set -a
    # shellcheck disable=SC1090
    source "$RUNTIME_ENV"
    set +a
fi

CHAT_PORT=${VLLM_CHAT_PORT:-8100}
EMBEDDING_PORT=${VLLM_EMBEDDING_PORT:-8101}
RAG_API_PORT=${RAG_API_PORT:-8001}
STOP_TIMEOUT=${SERVICE_STOP_TIMEOUT:-60}

command -v screen >/dev/null 2>&1 || {
    echo "找不到 screen，无法按会话名停止服务。" >&2
    exit 2
}
command -v ss >/dev/null 2>&1 || {
    echo "找不到 ss，无法确认服务端口是否释放。" >&2
    exit 2
}

screen_exists() {
    local session=$1
    screen -ls 2>/dev/null | grep -Eq "[[:space:]][0-9]+\.${session}[[:space:]]"
}

stop_screen() {
    local session=$1

    if ! screen_exists "$session"; then
        echo "[$session] Screen 会话不存在，跳过。"
        return
    fi

    echo "[$session] 正在停止……"
    screen -S "$session" -X quit
    local deadline=$((SECONDS + STOP_TIMEOUT))
    while screen_exists "$session"; do
        if ((SECONDS >= deadline)); then
            echo "[$session] 在 ${STOP_TIMEOUT} 秒内没有退出，请检查进程。" >&2
            return 1
        fi
        sleep 1
    done
    echo "[$session] 已停止。"
}

port_is_listening() {
    local port=$1
    ss -lntH 2>/dev/null | awk -v suffix=":${port}" '$4 ~ suffix "$" { found=1 } END { exit !found }'
}

wait_for_port_release() {
    local label=$1
    local port=$2
    local deadline=$((SECONDS + STOP_TIMEOUT))

    while port_is_listening "$port"; do
        if ((SECONDS >= deadline)); then
            echo "$label 的端口 $port 仍被占用；未自动终止未知进程。" >&2
            return 1
        fi
        sleep 1
    done
    echo "$label 的端口 $port 已释放。"
}

cd "$REPO_ROOT"

overall_status=0

echo "[1/3] 停止 FastAPI"
stop_screen rag-api || overall_status=1
wait_for_port_release "FastAPI" "$RAG_API_PORT" || overall_status=1

echo "[2/3] 停止 vLLM 生成服务"
stop_screen vllm-chat || overall_status=1
wait_for_port_release "生成服务" "$CHAT_PORT" || overall_status=1

echo "[3/3] 停止 vLLM 向量服务"
stop_screen vllm-embedding || overall_status=1
wait_for_port_release "向量服务" "$EMBEDDING_PORT" || overall_status=1

echo
if ((overall_status == 0)); then
    echo "三个服务均已停止。Docker 前端容器未被停止。"
    echo "重新启动：deploy/start-services.sh"
else
    echo "停止流程已执行完毕，但仍有会话或端口需要人工检查。" >&2
    echo "请运行：screen -ls" >&2
    echo "以及：ss -lntp | grep -E ':(8001|8100|8101)\\b'" >&2
fi

exit "$overall_status"
