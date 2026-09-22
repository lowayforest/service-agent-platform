#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

require_value EMBEDDING_MODEL_PATH
require_value EMBEDDING_API_KEY

export CUDA_VISIBLE_DEVICES=${VLLM_EMBEDDING_GPU:-1}

exec "$VLLM_BIN" serve "$EMBEDDING_MODEL_PATH" \
    --host 127.0.0.1 \
    --port "${VLLM_EMBEDDING_PORT:-8101}" \
    --api-key "$EMBEDDING_API_KEY" \
    --served-model-name "${EMBEDDING_SERVED_MODEL_NAME:-qwen3-embedding}" \
    --runner pooling \
    --max-model-len "${VLLM_MAX_MODEL_LEN:-8192}" \
    --gpu-memory-utilization "${VLLM_EMBEDDING_GPU_MEMORY_UTILIZATION:-0.50}"
