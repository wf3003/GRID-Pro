#!/bin/bash
# ============================================
# GRID-Pro - 一键启动/停止脚本
# ============================================

set -e

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

PID_FILE="data/server.pid"
LOG_FILE="logs/server.log"

# 停止功能
stop_server() {
    if [ ! -f "$PID_FILE" ]; then
        echo -e "${YELLOW}⚠ 服务器未运行 (PID 文件不存在)${NC}"
        return
    fi
    
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo -e "${YELLOW}⏹ 正在停止服务器 (PID: $PID)...${NC}"
        kill "$PID" 2>/dev/null
        sleep 2
        # 如果没关掉，强制杀掉
        if kill -0 "$PID" 2>/dev/null; then
            kill -9 "$PID" 2>/dev/null
            echo -e "${GREEN}✅ 服务器已强制停止${NC}"
        else
            echo -e "${GREEN}✅ 服务器已停止${NC}"
        fi
    else
        echo -e "${YELLOW}⚠ 进程不存在${NC}"
    fi
    rm -f "$PID_FILE"
}

# 如果参数是 stop，执行停止
if [ "$1" = "stop" ]; then
    stop_server
    exit 0
fi

# 如果参数是 restart，先停止再继续启动
if [ "$1" = "restart" ]; then
    stop_server
    echo ""
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}  重新启动...${NC}"
    echo -e "${GREEN}========================================${NC}"
fi

echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  GRID-Pro v1.0${NC}"
echo -e "${GREEN}========================================${NC}"

# 检查是否已在运行
if [ -f "$PID_FILE" ]; then
    PID=$(cat "$PID_FILE")
    if kill -0 "$PID" 2>/dev/null; then
        echo -e "${YELLOW}⚠ 服务器已在运行 (PID: $PID)${NC}"
        echo -e "${GREEN}🌐 访问地址: http://localhost:3000${NC}"
        echo -e "${GREEN}📝 查看日志: tail -f ${LOG_FILE}${NC}"
        echo -e "${GREEN}🛑 停止服务: $0 stop${NC}"
        exit 0
    else
        echo -e "${YELLOW}⚠ 检测到残留 PID 文件，正在清理...${NC}"
        rm -f "$PID_FILE"
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

# 后台启动 Web 服务器
nohup python3 -m src.web_api > "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"

sleep 2

# 检查是否启动成功
if kill -0 $(cat "$PID_FILE") 2>/dev/null; then
    echo -e "${GREEN}========================================${NC}"
    echo -e "${GREEN}✅ 服务器已后台启动 (PID: $(cat $PID_FILE))${NC}"
    echo -e "${GREEN}🌐 访问地址: http://localhost:3000${NC}"
    echo -e "${GREEN}📝 查看日志: tail -f ${LOG_FILE}${NC}"
    echo -e "${GREEN}🛑 停止服务: $0 stop${NC}"
    echo -e "${GREEN}🔄 重启服务: $0 restart${NC}"
    echo -e "${GREEN}========================================${NC}"
else
    echo -e "${RED}❌ 启动失败，请查看日志: tail -f ${LOG_FILE}${NC}"
    rm -f "$PID_FILE"
    exit 1
fi
