#!/bin/bash
# ============================================
# 网格交易控制台 - 一键启动脚本
# ============================================

set -e

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  网格交易控制台 v1.0${NC}"
echo -e "${GREEN}========================================${NC}"

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo -e "${YELLOW}⚠ 未检测到虚拟环境，正在创建...${NC}"
    python3 -m venv venv
    source venv/bin/activate
    pip install -r requirements.txt
    echo -e "${GREEN}✅ 虚拟环境已创建${NC}"
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
