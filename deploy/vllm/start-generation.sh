#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

require_value CHAT_MODEL_PATH
require_value CHAT_API_KEY

export CUDA_VISIBLE_DEVICES=${VLLM_CHAT_GPU:-0}

exec "$VLLM_BIN" serve "$CHAT_MODEL_PATH" \
    --host 127.0.0.1 \
    --port "${VLLM_CHAT_PORT:-8100}" \
    --api-key "$CHAT_API_KEY" \
    --served-model-name "${CHAT_SERVED_MODEL_NAME:-qwen3.5}" \
    --tensor-parallel-size 1 \
    --max-model-len "${VLLM_MAX_MODEL_LEN:-8192}" \
    --gpu-memory-utilization "${VLLM_CHAT_GPU_MEMORY_UTILIZATION:-0.80}" \
    --reasoning-parser qwen3 \
    --default-chat-template-kwargs '{"enable_thinking": false}' \
    --language-model-only
