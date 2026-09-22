#!/usr/bin/env bash

set -euo pipefail

VLLM_DEPLOY_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
VLLM_REPO_ROOT=$(cd -- "$VLLM_DEPLOY_DIR/../.." && pwd)
VLLM_RUNTIME_ENV=${VLLM_RUNTIME_ENV:-"$VLLM_DEPLOY_DIR/runtime.env"}

if [[ ! -f "$VLLM_RUNTIME_ENV" ]]; then
    echo "缺少运行配置：$VLLM_RUNTIME_ENV" >&2
    echo "请先复制 deploy/vllm/runtime.env.example 并填写模型路径与密钥。" >&2
    exit 2
fi

set -a
# shellcheck disable=SC1090
source "$VLLM_RUNTIME_ENV"
set +a

VLLM_BIN=${VLLM_BIN:-"$VLLM_REPO_ROOT/.venv-vllm/bin/vllm"}
if [[ ! -x "$VLLM_BIN" ]]; then
    echo "找不到 vLLM 可执行文件：$VLLM_BIN" >&2
    echo "请先完成 .venv-vllm 安装。" >&2
    exit 2
fi

require_value() {
    local name=$1
    local value=${!name:-}
    if [[ -z "$value" || "$value" == replace-with-* ]]; then
        echo "$name 尚未正确配置。" >&2
        exit 2
    fi
}
