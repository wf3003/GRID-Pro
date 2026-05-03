"""交易所工厂"""
from typing import Dict, Optional

from src.exchanges.base import ExchangeBase
from src.exchanges.binance import BinanceExchange
from src.exchanges.gateio import GateIOExchange
from src.models.config import ExchangeConfig


class ExchangeFactory:
    """交易所工厂类"""
    
    _exchanges: Dict[str, ExchangeBase] = {}
    
    @classmethod
    def create_exchange(cls, config: ExchangeConfig) -> ExchangeBase:
        """创建交易所实例（不缓存，每次创建新实例）"""
        name = config.name.lower()
        
        if name == "binance":
            exchange = BinanceExchange(
                api_key=config.api_key,
                api_secret=config.api_secret,
                base_url=config.base_url
            )
        elif name == "gateio":
            exchange = GateIOExchange(
                api_key=config.api_key,
                api_secret=config.api_secret,
                base_url=config.base_url
            )
        else:
            raise ValueError(f"Unsupported exchange: {name}")
        
        return exchange
    
    @classmethod
    def get_exchange(cls, name: str) -> Optional[ExchangeBase]:
        """获取交易所实例"""
        return cls._exchanges.get(name.lower())
    
    @classmethod
    async def close_all(cls):
        """关闭所有交易所连接"""
        for exchange in cls._exchanges.values():
            await exchange.close()
        cls._exchanges.clear()
