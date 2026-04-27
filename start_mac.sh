#!/bin/bash
# OAS macOS/Linux 启动脚本

# 获取脚本所在目录
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$SCRIPT_DIR"

# 颜色定义
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

# 检测操作系统
OS_TYPE=$(uname -s)
echo -e "${GREEN}=== OnmyojiAutoScript 启动脚本 ===${NC}"
echo -e "${GREEN}操作系统: $OS_TYPE${NC}"
echo ""

# 检查 Python 3.10
if command -v python3.10 &> /dev/null; then
    PYTHON_CMD="python3.10"
elif command -v python3 &> /dev/null; then
    PYTHON_VERSION=$(python3 --version 2>&1 | awk '{print $2}' | cut -d. -f1,2)
    if [[ "$PYTHON_VERSION" == "3.10" ]]; then
        PYTHON_CMD="python3"
    else
        echo -e "${RED}错误: 需要 Python 3.10，当前版本: $PYTHON_VERSION${NC}"
        if [[ "$OS_TYPE" == "Darwin" ]]; then
            echo -e "${YELLOW}请安装 Python 3.10: brew install python@3.10${NC}"
        elif [[ "$OS_TYPE" == "Linux" ]]; then
            echo -e "${YELLOW}请安装 Python 3.10: sudo apt install python3.10 (Ubuntu/Debian)${NC}"
            echo -e "${YELLOW}或: sudo yum install python3.10 (CentOS/RHEL)${NC}"
        fi
        exit 1
    fi
else
    echo -e "${RED}错误: 未找到 Python 3.10${NC}"
    if [[ "$OS_TYPE" == "Darwin" ]]; then
        echo -e "${YELLOW}请安装: brew install python@3.10${NC}"
    elif [[ "$OS_TYPE" == "Linux" ]]; then
        echo -e "${YELLOW}请安装: sudo apt install python3.10 python3.10-venv (Ubuntu/Debian)${NC}"
        echo -e "${YELLOW}或: sudo yum install python3.10 (CentOS/RHEL)${NC}"
    fi
    exit 1
fi

echo -e "${GREEN}✓ 找到 Python: $PYTHON_CMD${NC}"
$PYTHON_CMD --version

# 检查 Git 更新（如果启用了 AutoUpdate）
if [ -d ".git" ]; then
    # 读取 deploy.yaml 中的配置
    AUTO_UPDATE=$(grep "AutoUpdate:" config/deploy.yaml | awk '{print $2}')
    KEEP_LOCAL=$(grep "KeepLocalChanges:" config/deploy.yaml | awk '{print $2}')
    TARGET_BRANCH=$(grep "Branch:" config/deploy.yaml | awk '{print $2}')

    if [[ "$AUTO_UPDATE" == "true" ]]; then
        echo -e "${YELLOW}检查更新...${NC}"
        echo -e "${YELLOW}目标分支: $TARGET_BRANCH${NC}"

        # 获取当前分支
        CURRENT_BRANCH=$(git branch --show-current)

        # 获取远程更新
        git fetch origin 2>/dev/null

        # 如果当前分支与目标分支不一致，先切换分支
        if [ "$CURRENT_BRANCH" != "$TARGET_BRANCH" ]; then
            echo -e "${YELLOW}当前分支 ($CURRENT_BRANCH) 与配置不符，切换到 $TARGET_BRANCH...${NC}"

            if [[ "$KEEP_LOCAL" == "true" ]]; then
                git stash
            fi

            git checkout "$TARGET_BRANCH" 2>/dev/null || git checkout -b "$TARGET_BRANCH" origin/"$TARGET_BRANCH"

            if [[ "$KEEP_LOCAL" == "true" ]]; then
                git stash pop 2>/dev/null
            fi
        fi

        # 检查是否有更新
        LOCAL=$(git rev-parse HEAD)
        REMOTE=$(git rev-parse origin/"$TARGET_BRANCH" 2>/dev/null || echo $LOCAL)

        if [ "$LOCAL" != "$REMOTE" ]; then
            echo -e "${YELLOW}发现新版本，正在更新...${NC}"

            if [[ "$KEEP_LOCAL" == "true" ]]; then
                # 保留本地修改
                git stash
                git pull origin "$TARGET_BRANCH" --rebase
                git stash pop 2>/dev/null
            else
                # 不保留本地修改，直接重置到远程分支
                git reset --hard origin/"$TARGET_BRANCH"
            fi

            if [ $? -eq 0 ]; then
                echo -e "${GREEN}✓ 更新成功${NC}"
            else
                echo -e "${RED}更新失败，继续使用当前版本${NC}"
            fi
        else
            echo -e "${GREEN}✓ 已是最新版本${NC}"
        fi
    fi
else
    echo -e "${YELLOW}提示: 不是 Git 仓库，跳过更新检查${NC}"
fi
echo ""

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}未找到虚拟环境，正在创建...${NC}"
    $PYTHON_CMD -m venv venv
    if [ $? -ne 0 ]; then
        echo -e "${RED}创建虚拟环境失败${NC}"
        if [[ "$OS_TYPE" == "Linux" ]]; then
            echo -e "${YELLOW}提示: 在 Linux 上可能需要安装 python3.10-venv${NC}"
            echo -e "${YELLOW}运行: sudo apt install python3.10-venv${NC}"
        fi
        exit 1
    fi
    echo -e "${GREEN}✓ 虚拟环境创建成功${NC}"
fi

# 激活虚拟环境
source venv/bin/activate

# 检查依赖是否已安装
if ! python -c "import fastapi" 2>/dev/null; then
    echo -e "${YELLOW}正在安装依赖...${NC}"

    # 先升级 pip 和安装 wheel
    pip install --upgrade pip wheel

    # 直接从 requirements.txt 读取并过滤，不创建临时文件
    echo -e "${YELLOW}正在处理依赖列表...${NC}"
    grep -v "pywin32" requirements.txt | sed 's/lxml==5.0.0/lxml==4.9.3/' | pip install -r /dev/stdin

    if [ $? -ne 0 ]; then
        echo -e "${RED}依赖安装失败${NC}"
        exit 1
    fi
    echo -e "${GREEN}✓ 依赖安装完成${NC}"
fi

# 启动服务
echo ""
echo -e "${GREEN}=== 启动 OAS 服务 ===${NC}"
echo -e "${YELLOW}访问地址: http://localhost:22270${NC}"
echo -e "${YELLOW}按 Ctrl+C 停止服务${NC}"
echo ""

python server.py "$@"
