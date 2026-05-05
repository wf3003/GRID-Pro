"""交易所基类"""
from abc import ABC, abstractmethod
from decimal import Decimal
from typing import Optional, List, Tuple
from src.models.trading import Order, OrderSide, Ticker, Balance, Kline


class ExchangeBase(ABC):
    """交易所抽象基类"""
    
    def __init__(self, name: str, api_key: str, api_secret: str, base_url: str = ""):
        self.name = name
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url
    
    @abstractmethod
    async def get_ticker(self, symbol: str) -> Ticker:
        """获取行情数据"""
        pass
    
    @abstractmethod
    async def get_balance(self, asset: str) -> Balance:
        """获取账户余额"""
        pass
    
    @abstractmethod
    async def create_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        price: Decimal,
        quantity: Decimal
    ) -> Order:
        """创建限价单"""
        pass
    
    @abstractmethod
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """取消订单"""
        pass
    
    @abstractmethod
    async def get_order(self, symbol: str, order_id: str) -> Optional[Order]:
        """获取订单状态"""
        pass
    
    @abstractmethod
    async def get_open_orders(self, symbol: str) -> List[Order]:
        """获取当前挂单"""
        pass
    
    @abstractmethod
    async def get_trading_fee(self, symbol: str) -> Decimal:
        """获取交易费率"""
        pass
    
    @abstractmethod
    async def get_symbol_precision(self, symbol: str) -> Tuple[int, int]:
        """获取交易对精度 (价格精度, 数量精度)"""
        pass
    
    async def get_min_order_amount(self, symbol: str) -> Decimal:
        """获取最小订单金额（计价货币，如 USDT）
        
        默认返回 0，子类可覆盖此方法返回交易所实际的最小金额。
        """
        return Decimal("0")
    
    @abstractmethod
    async def get_trading_pairs(self, quote_asset: str = "USDT") -> List[dict]:
        """获取可交易币种列表
        
        Args:
            quote_asset: 计价资产，如 "USDT"
            
        Returns:
            List[dict]: 每个元素包含:
                - symbol: 交易对符号，如 "BTC/USDT"
                - base_asset: 基础资产，如 "BTC"
                - quote_asset: 计价资产，如 "USDT"
                - price_precision: 价格精度
                - qty_precision: 数量精度
                - min_qty: 最小交易数量
                - min_amount: 最小交易金额
                - status: 交易状态，如 "TRADING"
        """
        pass
    
    @abstractmethod
    async def get_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 100
    ) -> List[Kline]:
        """获取 K 线数据
        
        Args:
            symbol: 交易对符号，如 "BTC/USDT"
            interval: K 线周期，如 1m, 5m, 15m, 30m, 1h, 4h, 1d, 1w
            limit: 获取数量，默认 100
            
        Returns:
            List[Kline]: K 线数据列表
        """
        pass
