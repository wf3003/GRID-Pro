"""交易数据模型"""
from decimal import Decimal
from typing import Optional, List
from enum import Enum
from datetime import datetime
from pydantic import BaseModel


class OrderSide(str, Enum):
    BUY = "buy"
    SELL = "sell"


class OrderStatus(str, Enum):
    PENDING = "pending"
    OPEN = "open"
    FILLED = "filled"
    PARTIALLY_FILLED = "partially_filled"
    CANCELLED = "cancelled"
    FAILED = "failed"


class OrderType(str, Enum):
    LIMIT = "limit"
    MARKET = "market"


class Order(BaseModel):
    """订单模型"""
    id: str
    exchange: str
    symbol: str
    side: OrderSide
    price: Decimal
    quantity: Decimal
    filled_quantity: Decimal = Decimal("0")
    status: OrderStatus = OrderStatus.PENDING
    type: OrderType = OrderType.LIMIT
    created_at: datetime = datetime.now()
    updated_at: datetime = datetime.now()
    grid_level: Optional[int] = None  # 所属网格层级


class GridLevel(BaseModel):
    """网格层级"""
    level: int  # 层级编号
    buy_price: Decimal  # 买入价格
    sell_price: Decimal  # 卖出价格
    quantity: Decimal  # 每层数量
    buy_order: Optional[Order] = None  # 当前买入订单
    sell_order: Optional[Order] = None  # 当前卖出订单
    is_active: bool = False  # 是否激活


class Ticker(BaseModel):
    """行情数据"""
    exchange: str
    symbol: str
    bid: Decimal  # 买一价
    ask: Decimal  # 卖一价
    last: Decimal  # 最新价
    volume: Decimal  # 24h成交量
    high_24h: Optional[Decimal] = None  # 24h最高价
    low_24h: Optional[Decimal] = None   # 24h最低价
    timestamp: datetime = datetime.now()


class Balance(BaseModel):
    """账户余额"""
    exchange: str
    asset: str
    free: Decimal
    locked: Decimal
    total: Decimal


class Kline(BaseModel):
    """K 线数据"""
    exchange: str
    symbol: str
    interval: str  # 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w
    open_time: datetime
    close_time: datetime
    open: Decimal
    high: Decimal
    low: Decimal
    close: Decimal
    volume: Decimal
    quote_volume: Decimal = Decimal("0")
    taker_buy_volume: Decimal = Decimal("0")  # 主动买入成交量
    taker_buy_quote_volume: Decimal = Decimal("0")  # 主动买入成交额


class GridStatus(BaseModel):
    """网格状态"""
    symbol: str
    exchange: str
    current_price: Decimal = Decimal("0")
    grid_levels: List[GridLevel] = []
    active_buy_orders: int = 0
    active_sell_orders: int = 0
    total_pnl: Decimal = Decimal("0")
    total_fees: Decimal = Decimal("0")
    total_trades: int = 0
    is_running: bool = False
    last_adjust_time: Optional[datetime] = None
