"""Web API 服务 - FastAPI + WebSocket"""
import asyncio
import json
import os
from typing import Dict, List, Optional, Any
from contextlib import asynccontextmanager
from datetime import datetime, timedelta

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Depends, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from loguru import logger
from jose import JWTError, jwt
from passlib.context import CryptContext
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from cryptography.fernet import Fernet

from src.database import Database
from src.exchanges.factory import ExchangeFactory
from src.exchanges.base import ExchangeBase
from src.models.config import GridConfig, ExchangeConfig
from src.strategies.grid_strategy import GridStrategy
from src.analysis.market_analyzer import MarketAnalyzer


# ========== JWT 配置 ==========
# 先加载 .env 文件，确保 JWT_SECRET 被读取
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

# 如果环境变量没设置，自动生成一个随机密钥并持久化到 .env
_DEFAULT_SECRET = os.getenv("JWT_SECRET")
if not _DEFAULT_SECRET:
    import secrets
    _DEFAULT_SECRET = secrets.token_hex(32)
    os.environ["JWT_SECRET"] = _DEFAULT_SECRET
    # 持久化到 .env 文件，确保重启后密钥不变
    env_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    try:
        with open(env_path, "a") as f:
            f.write(f"\n# JWT 密钥（自动生成，请勿修改）\nJWT_SECRET={_DEFAULT_SECRET}\n")
    except Exception:
        pass
SECRET_KEY = _DEFAULT_SECRET

ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 小时

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer(auto_error=False)
limiter = Limiter(key_func=get_remote_address)


# ========== API Key 加密 ==========
# 用 JWT_SECRET 派生 Fernet 密钥来加密 API Secret
import base64
import hashlib

def _get_fernet_key() -> bytes:
    """从 JWT_SECRET 派生 Fernet 密钥"""
    digest = hashlib.sha256(SECRET_KEY.encode()).digest()
    return base64.urlsafe_b64encode(digest)

_fernet = Fernet(_get_fernet_key())

def _encrypt_secret(secret: str) -> str:
    """加密 API Secret"""
    if not secret:
        return ""
    return _fernet.encrypt(secret.encode()).decode()

def _decrypt_secret(encrypted: str) -> str:
    """解密 API Secret"""
    if not encrypted:
        return ""
    try:
        return _fernet.decrypt(encrypted.encode()).decode()
    except Exception:
        return ""


def _get_api_keys(exchange: str) -> tuple:
    """从环境变量获取 API Key（自动解密 Secret）"""
    prefix = exchange.upper()
    api_key = os.getenv(f"{prefix}_API_KEY", "")
    encrypted_secret = os.getenv(f"{prefix}_API_SECRET", "")
    # 尝试解密，如果解密失败则返回原始值（兼容旧数据）
    api_secret = _decrypt_secret(encrypted_secret)
    if not api_secret:
        api_secret = encrypted_secret
    return (api_key, api_secret)


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


# ========== JWT 辅助函数 ==========

def create_access_token(data: dict) -> str:
    """创建 JWT token"""
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)


async def get_current_user(credentials: Optional[HTTPAuthorizationCredentials] = Depends(security)):
    """验证 JWT token，返回用户名"""
    if credentials is None:
        raise HTTPException(status_code=401, detail="请先登录")
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        username = payload.get("sub")
        if username is None:
            raise HTTPException(status_code=401, detail="Token 无效")
        return username
    except JWTError:
        raise HTTPException(status_code=401, detail="Token 已过期或无效")


class GridBotAPI:
    """GRID-Pro API 服务"""
    
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
            # 启动时自动恢复网格
            try:
                await self._auto_recover_grid()
            except Exception as e:
                logger.error(f"自动恢复网格失败: {e}")
            yield
            # 关闭时停止策略
            if self.strategy:
                await self.strategy.stop()
        
        app = FastAPI(title="GRID-Pro", lifespan=lifespan)
        self.app = app
        
        # 添加速率限制
        app.state.limiter = limiter
        app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)
        
        # CORS 保护 - 只允许同源访问
        app.add_middleware(
            CORSMiddleware,
            allow_origins=[],
            allow_credentials=False,
            allow_methods=[],
            allow_headers=[],
        )
        
        # 挂载静态文件
        import os
        static_dir = os.path.join(os.path.dirname(__file__), "static")
        if os.path.exists(static_dir):
            app.mount("/static", StaticFiles(directory=static_dir), name="static")
        
        # ========== 公开接口（无需登录） ==========
        
        @app.get("/")
        async def root():
            return FileResponse(os.path.join(static_dir, "login.html"))
        
        @app.get("/app")
        async def app_page():
            return FileResponse(os.path.join(static_dir, "index.html"))
        
        @app.post("/api/register")
        @limiter.limit("5/minute")
        async def register(request: Request, data: dict):
            """注册"""
            username = data.get("username", "").strip()
            password = data.get("password", "").strip()
            if not username or not password:
                raise HTTPException(400, "用户名和密码不能为空")
            if len(username) < 3 or len(username) > 20:
                raise HTTPException(400, "用户名长度 3-20 个字符")
            if len(password) < 6:
                raise HTTPException(400, "密码长度至少 6 位")
            
            password_hash = pwd_context.hash(password)
            if self.db.create_user(username, password_hash):
                logger.info(f"新用户注册: {username}")
                return {"success": True, "message": "注册成功"}
            else:
                raise HTTPException(400, "用户名已存在")
        
        @app.post("/api/login")
        @limiter.limit("10/minute")
        async def login(request: Request, data: dict):
            """登录"""
            username = data.get("username", "").strip()
            password = data.get("password", "").strip()
            if not username or not password:
                raise HTTPException(400, "用户名和密码不能为空")
            
            user = self.db.get_user(username)
            if not user or not pwd_context.verify(password, user["password_hash"]):
                raise HTTPException(401, "用户名或密码错误")
            
            token = create_access_token({"sub": username})
            logger.info(f"用户登录: {username}")
            return {"success": True, "token": token, "username": username}
        
        @app.get("/api/check-auth")
        async def check_auth(username: str = Depends(get_current_user)):
            """检查登录状态"""
            return {"authenticated": True, "username": username}
        
        # ========== 受保护的 API 接口 ==========
        
        @app.get("/api/status")
        async def get_status(username: str = Depends(get_current_user)):
            """获取当前状态"""
            # 检查是否有运行中的网格配置（即使内存状态丢失）
            active_config = self.db.get_active_grid_config()
            
            if not self.strategy or not self._running:
                # 如果数据库中有运行中的网格但内存中没有，尝试恢复
                if active_config and not self._running:
                    # 尝试异步恢复（不阻塞请求）
                    try:
                        asyncio.create_task(self._auto_recover_grid())
                    except Exception:
                        pass
                    return {"is_running": False, "has_active_config": True, 
                            "message": "检测到运行中的网格配置，正在尝试恢复..."}
                
                # 即使数据库状态是 stopped，也检查交易所是否有挂单
                # 获取所有配置（按时间倒序），检查最新的那个
                all_configs = self.db.get_grid_configs()
                if all_configs:
                    latest_config = all_configs[0]
                    # 尝试从交易所恢复挂单
                    try:
                        asyncio.create_task(self._auto_recover_from_exchange_orders(latest_config))
                    except Exception:
                        pass
                    return {"is_running": False, "has_active_config": False, 
                            "last_config_id": latest_config.get('id')}
                
                return {"is_running": False, "has_active_config": False}
            
            stats = self.strategy.get_statistics()
            
            # 获取数据库统计
            if self.strategy.db_config_id:
                trade_stats = self.db.get_trade_stats(self.strategy.db_config_id)
                trades = self.db.get_trades(self.strategy.db_config_id, limit=50)
                buy_orders = self.db.get_buy_orders(self.strategy.db_config_id)
                sell_orders = self.db.get_sell_orders(self.strategy.db_config_id)
                balance = self.db.get_latest_balance(self.strategy.db_config_id)
                config = self.db.get_grid_config_by_id(self.strategy.db_config_id)
                grid_trades = self.db.get_grid_trades(self.strategy.db_config_id, limit=50)
            else:
                trade_stats = {}
                trades = []
                buy_orders = []
                sell_orders = []
                balance = None
                config = None
                grid_trades = []
            
            # 计算运行时长（created_at 是 UTC 时间，now 用 UTC 计算）
            running_time = ""
            created_at = None
            if config and config.get('created_at'):
                created_at = config['created_at']
                try:
                    from datetime import datetime, timezone
                    # SQLite CURRENT_TIMESTAMP 是 UTC 时间
                    start = datetime.strptime(created_at, '%Y-%m-%d %H:%M:%S').replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    delta = now - start
                    days = delta.days
                    hours = delta.seconds // 3600
                    minutes = (delta.seconds % 3600) // 60
                    if days > 0:
                        running_time = f"{days}天{hours}小时{minutes}分钟"
                    elif hours > 0:
                        running_time = f"{hours}小时{minutes}分钟"
                    else:
                        running_time = f"{minutes}分钟"
                except:
                    running_time = "--"
            
            # 计算总收益（网格收益 + 浮动盈亏）
            total_profit = 0
            grid_profit = 0
            floating_pnl = 0
            if trade_stats:
                grid_profit = trade_stats.get('total_profit', 0)
            
            # 计算浮动盈亏
            if balance and stats.get('current_price'):
                current_price = stats['current_price']
                coin_value = (balance.get('coin_balance', 0) or 0) * current_price
                total_value = (balance.get('usdt_balance', 0) or 0) + coin_value
                initial_investment = config.get('total_investment', 0) if config else 0
                if initial_investment and initial_investment > 0:
                    floating_pnl = total_value - initial_investment
                    total_profit = grid_profit + floating_pnl
            
            # 计算年化收益率
            annualized_return = 0
            if config and config.get('created_at') and total_profit != 0:
                try:
                    from datetime import datetime
                    start = datetime.strptime(config['created_at'], '%Y-%m-%d %H:%M:%S')
                    now = datetime.now()
                    hours_running = (now - start).total_seconds() / 3600
                    if hours_running > 0 and config.get('total_investment', 0) > 0:
                        annualized_return = (total_profit / config['total_investment']) / (hours_running / (365 * 24)) * 100
                except:
                    pass
            
            return {
                "is_running": self._running,
                "strategy": stats,
                "trade_stats": trade_stats,
                "trades": trades,
                "buy_orders": buy_orders,
                "sell_orders": sell_orders,
                "balance": balance,
                "config": config,
                "grid_trades": grid_trades,
                "total_profit": round(total_profit, 2),
                "grid_profit": round(grid_profit, 2),
                "floating_pnl": round(floating_pnl, 2),
                "annualized_return": round(annualized_return, 2),
                "running_time": running_time,
                "created_at": created_at
            }

        
        @app.post("/api/start")
        async def start_grid(req: StartGridRequest, username: str = Depends(get_current_user)):
            """启动 GRID-Pro"""
            if self._running:
                raise HTTPException(400, "网格策略已在运行中")
            
            try:
                # 从环境变量获取 API Key
                api_key, api_secret = _get_api_keys(req.exchange)
                if not api_key or not api_secret:
                    raise HTTPException(400, f"{req.exchange} 的 API Key 未配置，请先在 API Key 设置中配置")
                
                # 创建交易所配置
                exchange_config = ExchangeConfig(
                    name=req.exchange,
                    api_key=api_key,
                    api_secret=api_secret
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
        
        @app.get("/api/check-min-amount")
        async def check_min_amount(exchange: str = "gateio", symbol: str = "OPG/USDT", 
                                   grid_count: int = 26, total_investment: float = 0,
                                   username: str = Depends(get_current_user)):
            """检查网格参数是否满足交易所最小订单金额要求"""
            try:
                api_key, api_secret = _get_api_keys(exchange)
                if not api_key or not api_secret:
                    return {"valid": False, "message": f"请先配置 {exchange} 的 API Key"}
                
                exchange_config = ExchangeConfig(
                    name=exchange,
                    api_key=api_key,
                    api_secret=api_secret
                )
                ex = ExchangeFactory.create_exchange(exchange_config)
                
                # 获取最小订单金额
                min_amount = await ex.get_min_order_amount(symbol)
                await ex.close()
                
                if min_amount <= 0:
                    return {"valid": True, "min_amount": 0, "per_grid_amount": 0, 
                            "message": "无法获取最小订单金额，将使用默认值"}
                
                # 计算每格金额
                half_grids = grid_count // 2
                buy_grids = max(1, half_grids - 1)
                sell_grids = max(1, half_grids - 1)
                total_grids = buy_grids + sell_grids
                
                per_grid_amount = total_investment / total_grids if total_investment > 0 else 10
                
                if per_grid_amount < min_amount:
                    # 计算建议的网格数量
                    max_grids = int(total_investment / min_amount) if total_investment > 0 else 0
                    suggested_grids = max(2, max_grids * 2) if max_grids >= 2 else grid_count
                    
                    return {
                        "valid": False,
                        "min_amount": float(min_amount),
                        "per_grid_amount": round(per_grid_amount, 2),
                        "message": f"每格金额 {per_grid_amount:.2f} USDT 小于交易所最小要求 {min_amount:.2f} USDT",
                        "suggestion": f"建议减少网格数量至 {suggested_grids} 格以下，或增加总投资额"
                    }
                
                return {
                    "valid": True,
                    "min_amount": float(min_amount),
                    "per_grid_amount": round(per_grid_amount, 2),
                    "message": f"每格金额 {per_grid_amount:.2f} USDT 满足最小要求 {min_amount:.2f} USDT"
                }
                
            except Exception as e:
                logger.error(f"检查最小金额失败: {e}")
                return {"valid": True, "min_amount": 0, "per_grid_amount": 0, 
                        "message": f"检查失败: {str(e)}"}
        
        @app.post("/api/stop")
        async def stop_grid(username: str = Depends(get_current_user)):
            """停止 GRID-Pro"""
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
        
        @app.get("/api/analysis")
        async def get_analysis(exchange: str = "binance", symbol: str = "BTC/USDT"):
            """获取多周期技术分析"""
            try:
                exchange_config = ExchangeConfig(
                    name=exchange,
                    api_key="",
                    api_secret=""
                )
                ex = ExchangeFactory.create_exchange(exchange_config)
                analyzer = MarketAnalyzer(ex)
                result = await analyzer.analyze(symbol)
                await ex.close()
                return result
            except Exception as e:
                logger.error(f"技术分析失败: {e}")
                return {"symbol": symbol, "exchange": exchange, "error": str(e), "intervals": {}}
        
        @app.get("/api/advice")
        async def get_trading_advice(exchange: str = "binance", symbol: str = "BTC/USDT"):
            """获取综合交易建议"""
            try:
                exchange_config = ExchangeConfig(
                    name=exchange,
                    api_key="",
                    api_secret=""
                )
                ex = ExchangeFactory.create_exchange(exchange_config)
                analyzer = MarketAnalyzer(ex)
                analysis = await analyzer.analyze(symbol)
                advice = analyzer.get_trading_advice(analysis)
                await ex.close()
                return advice
            except Exception as e:
                logger.error(f"获取交易建议失败: {e}")
                return {"score": 0, "summary": f"获取建议失败: {str(e)}", "suggestions": [], "risk_warnings": []}
        
        @app.get("/api/klines")
        async def get_klines(exchange: str = "binance", symbol: str = "BTC/USDT", interval: str = "1h", limit: int = 200):
            """获取 K 线数据"""
            try:
                exchange_config = ExchangeConfig(
                    name=exchange,
                    api_key="",
                    api_secret=""
                )
                ex = ExchangeFactory.create_exchange(exchange_config)
                klines = await ex.get_klines(symbol, interval=interval, limit=limit)
                await ex.close()
                # 将 Decimal 转换为 float 以便 JSON 序列化
                result = []
                for k in klines:
                    result.append({
                        "open_time": k.open_time.isoformat(),
                        "close_time": k.close_time.isoformat(),
                        "open": float(k.open),
                        "high": float(k.high),
                        "low": float(k.low),
                        "close": float(k.close),
                        "volume": float(k.volume),
                        "quote_volume": float(k.quote_volume),
                        "taker_buy_volume": float(k.taker_buy_volume),
                        "taker_buy_quote_volume": float(k.taker_buy_quote_volume)
                    })
                return {"symbol": symbol, "interval": interval, "klines": result}
            except Exception as e:
                logger.error(f"获取 K 线数据失败: {e}")
                return {"symbol": symbol, "interval": interval, "error": str(e), "klines": []}
        
        @app.post("/api/keys")
        async def save_api_keys(config: ApiKeyConfig, username: str = Depends(get_current_user)):
            """保存 API Key 到 .env 文件（Secret 加密存储）"""
            try:
                prefix = config.exchange.upper()
                _save_env_file(f"{prefix}_API_KEY", config.api_key)
                # 加密存储 Secret
                encrypted = _encrypt_secret(config.api_secret)
                _save_env_file(f"{prefix}_API_SECRET", encrypted)
                # 重新加载环境变量（存加密后的值）
                os.environ[f"{prefix}_API_KEY"] = config.api_key
                os.environ[f"{prefix}_API_SECRET"] = encrypted
                logger.info(f"{config.exchange} API Key 已保存（加密存储）")
                return {"success": True, "message": f"✅ {config.exchange} API Key 已保存"}
            except Exception as e:
                logger.error(f"保存 API Key 失败: {e}")
                return {"success": False, "message": f"❌ 保存失败: {str(e)}"}
        
        @app.get("/api/keys")
        async def get_api_keys(username: str = Depends(get_current_user)):
            """获取各交易所 API Key 配置状态（不返回 Secret）"""
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
        async def ping(exchange: str = "binance", username: str = Depends(get_current_user)):
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
        async def get_balance(exchange: str = "binance", asset: str = "USDT", username: str = Depends(get_current_user)):
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
        async def get_configs(username: str = Depends(get_current_user)):
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
    
    async def _auto_recover_grid(self):
        """自动恢复网格策略（服务重启时从数据库和交易所恢复）"""
        # 检查数据库是否有运行中的网格
        config = self.db.get_active_grid_config()
        if not config:
            logger.info("没有需要恢复的网格策略")
            return
        
        exchange_name = config.get('exchange', 'binance')
        symbol = config.get('symbol', '')
        logger.info(f"检测到运行中的网格: {symbol} @ {exchange_name}，尝试恢复...")
        
        # 获取 API Key
        api_key, api_secret = _get_api_keys(exchange_name)
        if not api_key or not api_secret:
            logger.warning(f"{exchange_name} API Key 未配置，无法恢复网格")
            self.db.update_grid_status(config['id'], "stopped")
            return
        
        try:
            # 创建交易所实例
            exchange_config = ExchangeConfig(
                name=exchange_name,
                api_key=api_key,
                api_secret=api_secret
            )
            self.exchange = ExchangeFactory.create_exchange(exchange_config)
            
            # 从交易所拉取当前挂单
            open_orders = await self.exchange.get_open_orders(symbol)
            
            if not open_orders:
                logger.warning(f"没有找到现有挂单，标记网格为已停止")
                self.db.update_grid_status(config['id'], "stopped")
                await self.exchange.close()
                self.exchange = None
                return
            
            # 创建网格配置
            grid_config = GridConfig(
                symbol=symbol,
                grid_count=config.get('grid_count', 26),
                price_spacing=config.get('price_spacing'),
                usdt_amount=config.get('total_investment'),
                coin_amount=config.get('base_quantity'),
                stop_loss_price=config.get('stop_loss_price'),
                total_investment=config.get('total_investment'),
                base_quantity=config.get('base_quantity'),
                profit_per_trade=config.get('profit_per_trade', 0.01),
                fee_rate=config.get('fee_rate', 0.002)
            )
            
            # 创建策略
            self.strategy = GridStrategy(self.exchange, grid_config)
            self.strategy.db_config_id = config['id']
            
            # 设置回调
            self.strategy.on_order_placed = self._on_order_placed
            self.strategy.on_order_filled_callback = self._on_order_filled
            self.strategy.on_grid_adjusted = self._on_grid_adjusted
            self.strategy.on_status_update = self._on_status_update
            
            # 从现有挂单恢复
            recovered = await self.strategy.recover_from_existing_orders(open_orders)
            
            if not recovered:
                logger.warning("网格恢复失败，标记为已停止")
                self.db.update_grid_status(config['id'], "stopped")
                await self.exchange.close()
                self.exchange = None
                self.strategy = None
                return
            
            # 启动主循环（不重新挂单，直接监控现有订单）
            self._running = True
            self.strategy._running = True
            self.strategy.status.is_running = True
            
            # 启动主循环
            task = asyncio.create_task(self.strategy._main_loop())
            self.strategy._tasks.append(task)
            
            # 启动调整循环
            adjust_task = asyncio.create_task(self.strategy._auto_adjust_loop())
            self.strategy._tasks.append(adjust_task)
            
            logger.info(f"✅ 网格策略已自动恢复: {symbol} @ {exchange_name} "
                        f"({len(open_orders)} 个挂单)")
            
        except Exception as e:
            logger.error(f"自动恢复网格失败: {e}")
            self.db.update_grid_status(config['id'], "stopped")
            if self.exchange:
                await self.exchange.close()
                self.exchange = None
            self.strategy = None
            self._running = False
    
    async def _auto_recover_from_exchange_orders(self, config: dict):
        """从交易所挂单恢复网格（即使数据库状态是 stopped）
        
        用于重新登录后，检测交易所是否有挂单并自动恢复。
        """
        if self._running or self.strategy:
            return  # 已经在运行
        
        if not config or 'id' not in config:
            logger.warning("配置无效，无法恢复")
            return
        
        exchange_name = config.get('exchange', 'binance')
        symbol = config.get('symbol', '')
        
        if not symbol:
            logger.warning("配置中没有交易对，无法恢复")
            return
        
        # 获取 API Key
        api_key, api_secret = _get_api_keys(exchange_name)
        if not api_key or not api_secret:
            return
        
        try:
            # 创建交易所实例
            exchange_config = ExchangeConfig(
                name=exchange_name,
                api_key=api_key,
                api_secret=api_secret
            )
            ex = ExchangeFactory.create_exchange(exchange_config)
            
            # 从交易所拉取当前挂单
            open_orders = await ex.get_open_orders(symbol)
            
            if not open_orders:
                await ex.close()
                return  # 没有挂单，不需要恢复
            
            logger.info(f"检测到 {len(open_orders)} 个现有挂单，自动恢复网格: {symbol} @ {exchange_name}")
            
            self.exchange = ex
            
            # 创建网格配置
            grid_config = GridConfig(
                symbol=symbol,
                grid_count=config.get('grid_count', 26),
                price_spacing=config.get('price_spacing'),
                usdt_amount=config.get('total_investment'),
                coin_amount=config.get('base_quantity'),
                stop_loss_price=config.get('stop_loss_price'),
                total_investment=config.get('total_investment'),
                base_quantity=config.get('base_quantity'),
                profit_per_trade=config.get('profit_per_trade', 0.01),
                fee_rate=config.get('fee_rate', 0.002)
            )
            
            # 创建策略
            self.strategy = GridStrategy(self.exchange, grid_config)
            self.strategy.db_config_id = config['id']
            
            # 设置回调
            self.strategy.on_order_placed = self._on_order_placed
            self.strategy.on_order_filled_callback = self._on_order_filled
            self.strategy.on_grid_adjusted = self._on_grid_adjusted
            self.strategy.on_status_update = self._on_status_update
            
            # 从现有挂单恢复
            recovered = await self.strategy.recover_from_existing_orders(open_orders)
            
            if not recovered:
                logger.warning("从交易所挂单恢复失败")
                await ex.close()
                self.exchange = None
                self.strategy = None
                return
            
            # 更新数据库状态为 running
            self.db.update_grid_status(config['id'], "running")
            
            # 启动主循环
            self._running = True
            self.strategy._running = True
            self.strategy.status.is_running = True
            
            task = asyncio.create_task(self.strategy._main_loop())
            self.strategy._tasks.append(task)
            
            adjust_task = asyncio.create_task(self.strategy._auto_adjust_loop())
            self.strategy._tasks.append(adjust_task)
            
            logger.info(f"✅ 从交易所挂单自动恢复网格: {symbol} @ {exchange_name} "
                        f"({len(open_orders)} 个挂单)")
            
            # 通知前端
            await self._broadcast({
                "type": "recovered",
                "data": {
                    "symbol": symbol,
                    "exchange": exchange_name,
                    "order_count": len(open_orders)
                }
            })
            
        except Exception as e:
            logger.error(f"从交易所挂单恢复失败: {e}")
            if self.exchange:
                try:
                    await self.exchange.close()
                except Exception:
                    pass
                self.exchange = None
            self.strategy = None
            self._running = False


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
