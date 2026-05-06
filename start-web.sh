#!/bin/bash

echo "╔════════════════════════════════════════╗"
echo "║     DailyClaw Web UI Server           ║"
echo "╚════════════════════════════════════════╝"
echo ""

# 检查虚拟环境
if [ -d ".venv" ]; then
    PYTHON_CMD="uv run python"
elif command -v uv &> /dev/null; then
    PYTHON_CMD="uv run python"
else
    PYTHON_CMD="python3"
fi

HOST="${WEB_HOST:-0.0.0.0}"
PORT="${WEB_PORT:-18080}"

echo "[模式] HTTP Web UI"
echo "[地址] http://${HOST}:${PORT}"
echo "[提示] 按 Ctrl+C 停止服务"
echo ""

${PYTHON_CMD} -m web_server.server --host "${HOST}" --port "${PORT}"
