#!/bin/bash
# ============================================
# GRID-Pro - 一键启动脚本
# ============================================

set -e

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  GRID-Pro v1.0${NC}"
echo -e "${GREEN}========================================${NC}"

# 检查并杀掉旧进程
OLD_PID=$(pgrep -f "python3 -m src.web_api" 2>/dev/null || true)
if [ -n "$OLD_PID" ]; then
    echo -e "${YELLOW}⚠ 检测到旧进程 (PID: $OLD_PID)，正在关闭...${NC}"
    kill $OLD_PID 2>/dev/null
    sleep 2
    # 如果没关掉，强制杀掉
    if kill -0 $OLD_PID 2>/dev/null; then
        kill -9 $OLD_PID 2>/dev/null
        echo -e "${GREEN}✅ 旧进程已强制关闭${NC}"
    else
        echo -e "${GREEN}✅ 旧进程已关闭${NC}"
    fi
fi

# 检查虚拟环境是否完整
VENV_OK=false
if [ -d "venv" ] && [ -f "venv/bin/activate" ]; then
    VENV_OK=true
fi

# 如果虚拟环境不完整或不存在，重新创建
if [ "$VENV_OK" = false ]; then
    echo -e "${YELLOW}⚠ 未检测到完整的虚拟环境，正在创建...${NC}"
    rm -rf venv
    python3 -m venv venv
    echo -e "${GREEN}✅ 虚拟环境已创建${NC}"
    echo -e "${YELLOW}📦 正在安装依赖...${NC}"
    source venv/bin/activate
    pip install -r requirements.txt
    echo -e "${GREEN}✅ 依赖安装完成${NC}"
else
    source venv/bin/activate
fi

# 检查 .env 文件
if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${YELLOW}⚠ 已从 .env.example 创建 .env 文件${NC}"
        echo -e "${YELLOW}⚠ 请编辑 .env 文件填入你的 API Key${NC}"
    fi
fi

# 加载环境变量
if [ -f ".env" ]; then
    set -a
    source .env
    set +a
fi

# 创建必要目录
mkdir -p data logs

echo -e "${GREEN}✅ 环境就绪，正在启动服务器...${NC}"
echo -e "${GREEN}🌐 访问地址: http://localhost:3000${NC}"
echo -e "${GREEN}========================================${NC}"

# 启动 Web 服务器
python3 -m src.web_api
