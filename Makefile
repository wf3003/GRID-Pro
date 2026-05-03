# ============================================
# GRID-Pro - Makefile
# ============================================

.PHONY: run install clean logs help

# 默认目标
help:
	@echo "============================================"
	@echo "  GRID-Pro - 命令帮助"
	@echo "============================================"
	@echo ""
	@echo "  make run       启动 Web 服务器 (http://localhost:3000)"
	@echo "  make install   安装/更新依赖"
	@echo "  make clean     清理数据 (数据库、日志)"
	@echo "  make logs      查看运行日志"
	@echo "  make help      显示此帮助"
	@echo ""

# 启动服务器
run:
	@echo "🚀 启动 GRID-Pro..."
	@bash run.sh

# 安装依赖
install:
	@echo "📦 安装依赖..."
	@python3 -m venv venv 2>/dev/null || true
	@bash -c "source venv/bin/activate && pip install -r requirements.txt"
	@echo "✅ 依赖安装完成"

# 清理数据
clean:
	@echo "🧹 清理数据..."
	@rm -rf data/*.db data/*.db-journal 2>/dev/null || true
	@rm -rf logs/*.log 2>/dev/null || true
	@echo "✅ 清理完成 (数据库和日志已删除)"

# 查看日志
logs:
	@echo "📋 日志文件:"
	@ls -la logs/ 2>/dev/null || echo "  暂无日志文件"
	@echo ""
	@echo "📝 最新日志:"
	@tail -f logs/*.log 2>/dev/null || echo "  暂无日志内容"
