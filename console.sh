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

# 2. 查找 adb（优先级: ADB_PATH > PATH > brew > 错误）
find_adb() {
    # 用户指定了路径
    if [ -n "$ADB_PATH" ] && [ -f "$ADB_PATH" ]; then
        echo "$ADB_PATH"
        return
    fi

    # 系统 PATH 里的 adb
    if command -v adb &> /dev/null; then
        echo "$(command -v adb)"
        return
    fi

    # Android SDK 自带的（最高优先级，版本最新）
    if [ -f "$HOME/Library/Android/sdk/platform-tools/adb" ]; then
        echo "$HOME/Library/Android/sdk/platform-tools/adb"
        return
    fi

    # brew 安装的
    if [ -f "/opt/homebrew/bin/adb" ]; then
        echo "/opt/homebrew/bin/adb"
        return
    fi
    if [ -f "/usr/local/bin/adb" ]; then
        echo "/usr/local/bin/adb"
        return
    fi

    echo ""
}

ADB_BIN=$(find_adb)
if [ -z "$ADB_BIN" ]; then
    echo -e "${RED}[FAIL]${NC} 未找到 adb"
    echo ""
    echo "  这个项目依赖 adb 连接 Android 设备/模拟器。"
    echo "  项目里 toolkit/ 带的是 Windows 版 adb.exe，无法在 macOS 运行。"
    echo ""
    echo "  请选择一种方式安装 macOS 版 adb:"
    echo ""
    echo "  方式1 (推荐): brew install android-platform-tools"
    echo "  方式2: 手动下载 platform-tools 后设置环境变量"
    echo "         export ADB_PATH=/path/to/adb"
    echo ""
    exit 1
fi

export ADB_PATH="$ADB_BIN"
echo -e "${GREEN}[OK]${NC} adb ($ADB_BIN)"

# 3. 检查端口占用
HOST="${OAS_HOST:-0.0.0.0}"
PORT="${OAS_PORT:-22270}"

if lsof -i ":${PORT}" &> /dev/null; then
    echo -e "${YELLOW}[WARN]${NC} 端口 ${PORT} 已被占用，尝试释放..."
    kill -9 $(lsof -ti:${PORT}) 2>/dev/null || true
    sleep 1
    echo -e "${GREEN}[OK]${NC} 端口已释放"
fi

# 4. 启动
echo ""
echo -e "  API:  ${GREEN}http://127.0.0.1:${PORT}${NC}"
echo -e "  Docs: ${GREEN}http://127.0.0.1:${PORT}/docs${NC}"
echo -e "  Ctrl+C 停止"
echo ""

python3 server.py --host "${HOST}" --port "${PORT}" 2>&1 || true
