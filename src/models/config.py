"""配置数据模型"""
from decimal import Decimal
from typing import Optional, List
from pydantic import BaseModel, Field


class ExchangeConfig(BaseModel):
    """交易所配置"""
    name: str
    api_key: str
    api_secret: str
    base_url: str = ""
    enable: bool = True


class GridConfig(BaseModel):
    """网格策略配置"""
    symbol: str  # 交易对，如 "BTC/USDT"
    upper_price: Optional[Decimal] = None  # 网格上限价格（仅用于显示最优范围）
    lower_price: Optional[Decimal] = None  # 网格下限价格（仅用于显示最优范围）
    grid_count: int = Field(default=26, ge=2, le=500, description="网格数量（上下各一半）")
    total_investment: Optional[Decimal] = None  # 总投资额（计价货币，如 USDT）
    base_quantity: Optional[Decimal] = None  # 每次交易币数量（与 total_investment 二选一）
    price_spacing: Optional[Decimal] = None  # 价格间隔百分比，如 0.5 表示 0.5%
    
    # 资金参数
    usdt_amount: Optional[Decimal] = None  # 用户手动输入的 USDT 数量
    coin_amount: Optional[Decimal] = None  # 用户手动输入的币数量
    
    # 止损参数
    stop_loss_price: Optional[Decimal] = None  # 止损价格，跌破自动停止
    
    # 利润和费用参数
    profit_per_trade: Decimal = Field(default=Decimal("0.01"), description="每次交易目标净利润率，如0.01表示1%")
    fee_rate: Decimal = Field(default=Decimal("0.002"), description="买卖总手续费率，如0.002表示0.2%")
    
    # 自动调整参数
    auto_adjust_enabled: bool = True  # 是否启用自动调整
    adjust_interval: int = 60  # 调整间隔（秒），默认1分钟
    price_deviation_threshold: Decimal = Field(default=Decimal("0.01"), description="价格偏离阈值，如0.01表示1%")
    
    # 风控参数
    max_open_orders: int = Field(default=10, description="最大同时挂单数")
    min_order_amount: Decimal = Field(default=Decimal("5"), description="最小订单金额（计价货币）")
    stop_loss_pct: Decimal = Field(default=Decimal("0.05"), description="止损百分比")
    take_profit_pct: Decimal = Field(default=Decimal("0.10"), description="止盈百分比")


class AppConfig(BaseModel):
    """应用配置"""
    exchanges: List[ExchangeConfig]
    grids: List[GridConfig]
    log_level: str = "INFO"
    data_dir: str = "./data"
