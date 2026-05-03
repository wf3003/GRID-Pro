"""Web API 服务 - FastAPI + WebSocket"""
import asyncio
import json
import os
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from loguru import logger

from src.database import Database
from src.exchanges.factory import ExchangeFactory
from src.exchanges.base import ExchangeBase
from src.models.config import GridConfig, ExchangeConfig
from src.strategies.grid_strategy import GridStrategy


# ========== 加载 .env 文件 ==========
def _load_env_file():
    """加载 .env 文件到环境变量"""
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" in line:
                    key, value = line.split("=", 1)
                    os.environ.setdefault(key.strip(), value.strip())

_load_env_file()


# ========== 请求/响应模型 ==========

class StartGridRequest(BaseModel):
    """启动网格请求"""
    exchange: str = "binance"
    symbol: str
    grid_count: int = 26
    price_spacing: Optional[float] = None  # 价格间隔百分比，如 0.5 表示 0.5%
    usdt_amount: Optional[float] = None  # 用户输入的 USDT 数量
    coin_amount: Optional[float] = None  # 用户输入的币数量
    stop_loss_price: Optional[float] = None  # 止损价格
    total_investment: Optional[float] = None
    base_quantity: Optional[float] = None
    profit_per_trade: float = 0.01
    fee_rate: float = 0.002


class ApiKeyConfig(BaseModel):
    """API Key 配置"""
    exchange: str
    api_key: str
    api_secret: str


class GridBotAPI:
    """网格交易 API 服务"""
    
    def __init__(self):
        self.app: Optional[FastAPI] = None
        self.db = Database()
        self.strategy: Optional[GridStrategy] = None
        self.exchange: Optional[ExchangeBase] = None
        self._running = False
        self._websockets: List[WebSocket] = []
    
    def create_app(self) -> FastAPI:
        """创建 FastAPI 应用"""
        
        @asynccontextmanager
        async def lifespan(app: FastAPI):
            yield
            # 关闭时停止策略
            if self.strategy:
                await self.strategy.stop()
        
        app = FastAPI(title="网格交易控制台", lifespan=lifespan)
        self.app = app
        
        # 挂载静态文件
        import os
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        if os.path.exists(static_dir):
            app.mount("/static", StaticFiles(directory=static_dir), name="static")
        
        @app.get("/")
        async def root():
            return FileResponse(os.path.join(static_dir, "index.html"))
        
        @app.get("/api/status")
        async def get_status():
            """获取当前状态"""
            if not self.strategy or not self._running:
                return {"is_running": False}
            
            stats = self.strategy.get_statistics()
            
            # 获取数据库统计
            if self.strategy.db_config_id:
                trade_stats = self.db.get_trade_stats(self.strategy.db_config_id)
                trades = self.db.get_trades(self.strategy.db_config_id, limit=50)
                buy_orders = self.db.get_buy_orders(self.strategy.db_config_id)
                sell_orders = self.db.get_sell_orders(self.strategy.db_config_id)
                balance = self.db.get_latest_balance(self.strategy.db_config_id)
            else:
                trade_stats = {}
                trades = []
                buy_orders = []
                sell_orders = []
                balance = None
            
            return {
                "is_running": self._running,
                "strategy": stats,
                "trade_stats": trade_stats,
                "trades": trades,
                "buy_orders": buy_orders,
                "sell_orders": sell_orders,
                "balance": balance
            }
        
        @app.post("/api/start")
        async def start_grid(req: StartGridRequest):
            """启动网格交易"""
            if self._running:
                raise HTTPException(400, "网格策略已在运行中")
            
            try:
                # 创建交易所配置
                exchange_config = ExchangeConfig(
                    name=req.exchange,
                    api_key="",  # 从环境变量读取
                    api_secret=""
                )
                
                # 创建交易所实例
                self.exchange = ExchangeFactory.create_exchange(exchange_config)
                
                # 创建网格配置
                grid_config = GridConfig(
                    symbol=req.symbol,
                    grid_count=req.grid_count,
                    price_spacing=req.price_spacing,
                    usdt_amount=req.usdt_amount,
                    coin_amount=req.coin_amount,
                    stop_loss_price=req.stop_loss_price,
                    total_investment=req.total_investment,
                    base_quantity=req.base_quantity,
                    profit_per_trade=req.profit_per_trade,
                    fee_rate=req.fee_rate
                )
                
                # 创建策略
                self.strategy = GridStrategy(self.exchange, grid_config)
                
                # 保存到数据库
                config_id = self.db.save_grid_config(
                    exchange=req.exchange,
                    symbol=req.symbol,
                    grid_count=req.grid_count,
                    price_spacing=req.price_spacing,
                    usdt_amount=req.usdt_amount,
                    coin_amount=req.coin_amount,
                    stop_loss_price=req.stop_loss_price,
                    total_investment=req.total_investment,
                    base_quantity=req.base_quantity,
                    profit_per_trade=req.profit_per_trade,
                    fee_rate=req.fee_rate
                )
                self.strategy.db_config_id = config_id
                
                # 设置回调
                self.strategy.on_order_placed = self._on_order_placed
                self.strategy.on_order_filled_callback = self._on_order_filled
                self.strategy.on_grid_adjusted = self._on_grid_adjusted
                self.strategy.on_status_update = self._on_status_update
                
                # 初始化并启动
                await self.strategy.initialize()
                asyncio.create_task(self.strategy.start())
                
                self._running = True
                self.db.update_grid_status(config_id, "running")
                
                logger.info(f"网格策略已启动: {req.symbol}")
                
                return {"success": True, "message": f"网格策略已启动: {req.symbol}"}
            
            except Exception as e:
                logger.error(f"启动失败: {e}")
                raise HTTPException(500, f"启动失败: {str(e)}")
        
        @app.post("/api/stop")
        async def stop_grid():
            """停止网格交易"""
            if not self._running or not self.strategy:
                raise HTTPException(400, "没有运行中的网格策略")
            
            try:
                await self.strategy.stop()
                self._running = False
                
                if self.strategy.db_config_id:
                    self.db.update_grid_status(self.strategy.db_config_id, "stopped")
                
                # 通知所有 WebSocket
                await self._broadcast({"type": "stopped"})
                
                logger.info("网格策略已停止")
                return {"success": True, "message": "网格策略已停止"}
            
            except Exception as e:
                logger.error(f"停止失败: {e}")
                raise HTTPException(500, f"停止失败: {str(e)}")
        
        @app.get("/api/ticker")
        async def get_ticker(exchange: str = "binance", symbol: str = "BTC/USDT"):
            """获取指定币种的实时价格"""
            try:
                exchange_config = ExchangeConfig(
                    name=exchange,
                    api_key="",
                    api_secret=""
                )
                ex = ExchangeFactory.create_exchange(exchange_config)
                ticker = await ex.get_ticker(symbol)
                await ex.close()
                return {
                    "symbol": symbol,
                    "price": float(ticker.last),
                    "bid": float(ticker.bid),
                    "ask": float(ticker.ask),
                    "high_24h": float(ticker.high_24h) if ticker.high_24h else 0,
                    "low_24h": float(ticker.low_24h) if ticker.low_24h else 0
                }
            except Exception as e:
                logger.error(f"获取价格失败: {e}")
                return {"symbol": symbol, "price": 0, "error": str(e)}
        
        @app.get("/api/symbols")
        async def get_symbols(exchange: str = "binance"):
            """获取交易所可交易币种列表"""
            try:
                # 创建交易所实例（不需要 API key 也能获取公开信息）
                exchange_config = ExchangeConfig(
                    name=exchange,
                    api_key="",
                    api_secret=""
                )
                ex = ExchangeFactory.create_exchange(exchange_config)
                pairs = await ex.get_trading_pairs("USDT")
                await ex.close()
                return {"symbols": pairs}
            except Exception as e:
                logger.error(f"获取币种列表失败: {e}")
                return {"symbols": [], "error": str(e)}
        
        def _get_api_keys(exchange: str) -> tuple:
            """从环境变量获取 API Key"""
            prefix = exchange.upper()
            return (
                os.getenv(f"{prefix}_API_KEY", ""),
                os.getenv(f"{prefix}_API_SECRET", "")
            )
        
        def _save_env_file(key: str, value: str):
            """保存单个环境变量到 .env 文件"""
            env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
            lines = []
            updated = False
            if os.path.exists(env_path):
                with open(env_path, "r") as f:
                    for line in f:
                        if line.startswith(f"{key}="):
                            lines.append(f"{key}={value}\n")
                            updated = True
                        else:
                            lines.append(line)
            if not updated:
                lines.append(f"{key}={value}\n")
            with open(env_path, "w") as f:
                f.writelines(lines)
        
        @app.post("/api/keys")
        async def save_api_keys(config: ApiKeyConfig):
            """保存 API Key 到 .env 文件"""
            try:
                prefix = config.exchange.upper()
                _save_env_file(f"{prefix}_API_KEY", config.api_key)
                _save_env_file(f"{prefix}_API_SECRET", config.api_secret)
                # 重新加载环境变量
                os.environ[f"{prefix}_API_KEY"] = config.api_key
                os.environ[f"{prefix}_API_SECRET"] = config.api_secret
                logger.info(f"{config.exchange} API Key 已保存")
                return {"success": True, "message": f"✅ {config.exchange} API Key 已保存"}
            except Exception as e:
                logger.error(f"保存 API Key 失败: {e}")
                return {"success": False, "message": f"❌ 保存失败: {str(e)}"}
        
        @app.get("/api/keys")
        async def get_api_keys():
            """获取各交易所 API Key 配置状态（返回 Key 用于前端回填，Secret 只返回是否已配置）"""
            result = {}
            for exchange in ["binance", "gateio"]:
                key, secret = _get_api_keys(exchange)
                result[exchange] = {
                    "configured": bool(key and secret),
                    "has_key": bool(key),
                    "api_key": key if key else "",
                    "api_secret_configured": bool(secret)
                }
            return {"keys": result}
        
        @app.get("/api/ping")
        async def ping(exchange: str = "binance"):
            """测试交易所连接"""
            try:
                api_key, api_secret = _get_api_keys(exchange)
                if not api_key or not api_secret:
                    return {"connected": False, "message": f"请先配置 {exchange} 的 API Key 和 Secret Key"}
                
                exchange_config = ExchangeConfig(
                    name=exchange,
                    api_key=api_key,
                    api_secret=api_secret
                )
                ex = ExchangeFactory.create_exchange(exchange_config)
                # 获取 USDT 余额验证连接
                balance = await ex.get_balance("USDT")
                await ex.close()
                return {
                    "connected": True,
                    "message": f"✅ 成功连接到 {exchange}",
                    "usdt_balance": float(balance.free)
                }
            except Exception as e:
                logger.error(f"连接测试失败: {e}")
                return {"connected": False, "message": f"❌ 连接失败: {str(e)}"}
        
        @app.get("/api/balance")
        async def get_balance(exchange: str = "binance", asset: str = "USDT"):
            """获取指定资产余额"""
            try:
                api_key, api_secret = _get_api_keys(exchange)
                if not api_key or not api_secret:
                    return {"asset": asset, "free": 0, "locked": 0, "total": 0, "error": "API Key 未配置"}
                
                exchange_config = ExchangeConfig(
                    name=exchange,
                    api_key=api_key,
                    api_secret=api_secret
                )
                ex = ExchangeFactory.create_exchange(exchange_config)
                balance = await ex.get_balance(asset)
                await ex.close()
                return {
                    "asset": asset,
                    "free": float(balance.free),
                    "locked": float(balance.locked),
                    "total": float(balance.total)
                }
            except Exception as e:
                logger.error(f"获取余额失败: {e}")
                return {"asset": asset, "free": 0, "locked": 0, "total": 0, "error": str(e)}
        
        @app.get("/api/configs")
        async def get_configs():
            """获取历史配置"""
            return {"configs": self.db.get_grid_configs()}
        
        @app.websocket("/ws")
        async def websocket_endpoint(websocket: WebSocket):
            """WebSocket 实时推送"""
            await websocket.accept()
            self._websockets.append(websocket)
            try:
                while True:
                    # 保持连接，接收心跳
                    data = await websocket.receive_text()
                    if data == "ping":
                        await websocket.send_text("pong")
            except WebSocketDisconnect:
                pass
            finally:
                self._websockets.remove(websocket)
        
        return app
    
    def _on_order_placed(self, order_data: dict):
        """挂单回调"""
        if self.strategy and self.strategy.db_config_id:
            self.db.save_order(
                grid_config_id=self.strategy.db_config_id,
                exchange_order_id=order_data['exchange_order_id'],
                symbol=order_data['symbol'],
                side=order_data['side'],
                price=order_data['price'],
                quantity=order_data['quantity'],
                amount=order_data['amount'],
                grid_level=order_data['grid_level']
            )
        
        asyncio.create_task(self._broadcast({
            "type": "order_placed",
            "data": order_data
        }))
    
    def _on_order_filled(self, trade_data: dict):
        """成交回调"""
        if self.strategy and self.strategy.db_config_id:
            self.db.save_trade(
                grid_config_id=self.strategy.db_config_id,
                exchange_order_id=trade_data['exchange_order_id'],
                symbol=trade_data['symbol'],
                side=trade_data['side'],
                price=trade_data['price'],
                quantity=trade_data['quantity'],
                amount=trade_data['amount'],
                fee=trade_data['fee'],
                profit=trade_data['profit']
            )
        
        asyncio.create_task(self._broadcast({
            "type": "trade",
            "data": trade_data
        }))
    
    def _on_grid_adjusted(self, adjust_data: dict):
        """网格调整回调"""
        asyncio.create_task(self._broadcast({
            "type": "grid_adjusted",
            "data": adjust_data
        }))
    
    def _on_status_update(self, stats: dict):
        """状态更新回调"""
        asyncio.create_task(self._broadcast({
            "type": "status_update",
            "data": stats
        }))
    
    async def _broadcast(self, message: dict):
        """广播消息到所有 WebSocket 客户端"""
        dead_sockets = []
        for ws in self._websockets:
            try:
                await ws.send_json(message)
            except Exception:
                dead_sockets.append(ws)
        
        for ws in dead_sockets:
            self._websockets.remove(ws)


# 创建全局实例
api = GridBotAPI()
fastapi_app = api.create_app()


def run_web_server(host: str = "0.0.0.0", port: int = 3000):
    """运行 Web 服务器"""
    import uvicorn
    logger.info(f"启动 Web 服务器: http://{host}:{port}")
    uvicorn.run(fastapi_app, host=host, port=port, log_level="info")


if __name__ == "__main__":
    run_web_server()
