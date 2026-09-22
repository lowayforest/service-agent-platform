#!/usr/bin/env bash

set -euo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# shellcheck source=common.sh
source "$SCRIPT_DIR/common.sh"

require_value CHAT_API_KEY
require_value EMBEDDING_API_KEY

chat_port=${VLLM_CHAT_PORT:-8100}
embedding_port=${VLLM_EMBEDDING_PORT:-8101}
chat_model=${CHAT_SERVED_MODEL_NAME:-qwen3.5}
embedding_model=${EMBEDDING_SERVED_MODEL_NAME:-qwen3-embedding}

echo "[1/3] 检查生成模型列表"
curl --fail --silent --show-error \
    -H "Authorization: Bearer $CHAT_API_KEY" \
    "http://127.0.0.1:${chat_port}/v1/models" >/dev/null

echo "[2/3] 检查非思考模式生成"
chat_response=$(curl --fail --silent --show-error \
    -H "Authorization: Bearer $CHAT_API_KEY" \
    -H 'Content-Type: application/json' \
    "http://127.0.0.1:${chat_port}/v1/chat/completions" \
    -d "{\"model\":\"${chat_model}\",\"messages\":[{\"role\":\"user\",\"content\":\"只回答：服务正常\"}],\"temperature\":0,\"max_tokens\":20,\"chat_template_kwargs\":{\"enable_thinking\":false}}")
CHAT_RESPONSE=$chat_response python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["CHAT_RESPONSE"])
content = payload["choices"][0]["message"]["content"].strip()
if not content:
    raise SystemExit("生成服务返回了空内容")
print("生成回答:", content)
PY

echo "[3/3] 检查向量数量与维度"
embedding_response=$(curl --fail --silent --show-error \
    -H "Authorization: Bearer $EMBEDDING_API_KEY" \
    -H 'Content-Type: application/json' \
    "http://127.0.0.1:${embedding_port}/v1/embeddings" \
    -d "{\"model\":\"${embedding_model}\",\"input\":[\"长江航道公共服务\"]}")
EMBEDDING_RESPONSE=$embedding_response python3 - <<'PY'
import json
import os

payload = json.loads(os.environ["EMBEDDING_RESPONSE"])
data = payload["data"]
if len(data) != 1 or not data[0]["embedding"]:
    raise SystemExit("向量服务返回格式不正确")
print("向量数量:", len(data), "向量维度:", len(data[0]["embedding"]))
PY

echo "vLLM 两个服务均通过冒烟测试。"
