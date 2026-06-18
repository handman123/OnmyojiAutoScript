#!/usr/bin/env bash
# OAS macOS 一键启动脚本
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}   OAS - OnmyojiAutoScript Server${NC}"
echo -e "${GREEN}========================================${NC}"

# 1. 检查 venv
if [ -f "venv/bin/activate" ]; then
    source venv/bin/activate
    echo -e "${GREEN}[OK]${NC} venv"
else
    echo -e "${RED}[FAIL]${NC} 未找到 venv，请先运行:"
    echo "  python3 -m venv venv"
    echo "  source venv/bin/activate"
    echo "  pip install -r requirements.txt"
    exit 1
fi

# 2. 检查端口占用
HOST="${OAS_HOST:-0.0.0.0}"
PORT="${OAS_PORT:-22270}"

if lsof -i ":${PORT}" &> /dev/null; then
    echo -e "${YELLOW}[WARN]${NC} 端口 ${PORT} 已被占用，尝试释放..."
    kill -9 $(lsof -ti:${PORT}) 2>/dev/null || true
    sleep 1
    echo -e "${GREEN}[OK]${NC} 端口已释放"
fi

# 3. 启动
echo ""
echo -e "  API:  ${GREEN}http://127.0.0.1:${PORT}${NC}"
echo -e "  Docs: ${GREEN}http://127.0.0.1:${PORT}/docs${NC}"
echo -e "  Ctrl+C 停止"
echo ""

python3 server.py --host "${HOST}" --port "${PORT}" 2>&1 || true
