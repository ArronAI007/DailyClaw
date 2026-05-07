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

# 检查端口占用，如有则 kill
if command -v lsof &> /dev/null; then
    PID=$(lsof -ti tcp:${PORT} 2>/dev/null)
    if [ -n "$PID" ]; then
        echo "[端口] ${PORT} 被占用 (PID: $PID)，正在释放..."
        kill -9 $PID 2>/dev/null
        sleep 1
    fi
fi

RELOAD_FLAG=""
if [ "${WEB_RELOAD:-true}" != "false" ]; then
    RELOAD_FLAG="--reload"
    echo "[热重载] 已启用 (WEB_RELOAD=false 可关闭)"
fi

${PYTHON_CMD} -m web_server.server --host "${HOST}" --port "${PORT}" ${RELOAD_FLAG}
