"""配置加载工具"""
import json
import os
from typing import List, Optional

from loguru import logger
from pydantic import ValidationError

from src.models.config import AppConfig, ExchangeConfig, GridConfig


class ConfigLoader:
    """配置加载器"""
    
    @staticmethod
    def load_from_file(path: str) -> Optional[AppConfig]:
        """从文件加载配置"""
        if not os.path.exists(path):
            logger.error(f"配置文件不存在: {path}")
            return None
        
        try:
            with open(path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            config = AppConfig(**data)
            logger.info(f"配置已加载: {path}")
            return config
            
        except json.JSONDecodeError as e:
            logger.error(f"配置文件格式错误: {e}")
            return None
        except ValidationError as e:
            logger.error(f"配置验证失败: {e}")
            return None
    
    @staticmethod
    def create_default_config() -> AppConfig:
        """创建默认配置"""
        return AppConfig(
            exchanges=[
                ExchangeConfig(
                    name="binance",
                    api_key="YOUR_BINANCE_API_KEY",
                    api_secret="YOUR_BINANCE_API_SECRET",
                    enable=False
                ),
                ExchangeConfig(
                    name="gateio",
                    api_key="YOUR_GATEIO_API_KEY",
                    api_secret="YOUR_GATEIO_API_SECRET",
                    enable=False
                )
            ],
            grids=[
                GridConfig(
                    symbol="BTC/USDT",
                    upper_price="70000",
                    lower_price="50000",
                    grid_count=10,
                    total_investment="1000",
                    auto_adjust_enabled=True,
                    adjust_interval=300,
                    price_deviation_threshold="0.02",
                    max_open_orders=5,
                    min_order_amount="5",
                    stop_loss_pct="0.05",
                    take_profit_pct="0.10"
                )
            ]
        )
    
    @staticmethod
    def save_default_config(path: str):
        """保存默认配置到文件"""
        config = ConfigLoader.create_default_config()
        
        # 转换为可序列化的字典
        config_dict = {
            "exchanges": [
                {
                    "name": e.name,
                    "api_key": e.api_key,
                    "api_secret": e.api_secret,
                    "base_url": e.base_url,
                    "enable": e.enable
                }
                for e in config.exchanges
            ],
            "grids": [
                {
                    "symbol": g.symbol,
                    "upper_price": str(g.upper_price),
                    "lower_price": str(g.lower_price),
                    "grid_count": g.grid_count,
                    "total_investment": str(g.total_investment),
                    "auto_adjust_enabled": g.auto_adjust_enabled,
                    "adjust_interval": g.adjust_interval,
                    "price_deviation_threshold": str(g.price_deviation_threshold),
                    "max_open_orders": g.max_open_orders,
                    "min_order_amount": str(g.min_order_amount),
                    "stop_loss_pct": str(g.stop_loss_pct),
                    "take_profit_pct": str(g.take_profit_pct)
                }
                for g in config.grids
            ],
            "log_level": config.log_level,
            "data_dir": config.data_dir
        }
        
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(config_dict, f, indent=2, ensure_ascii=False)
        
        logger.info(f"默认配置已保存到: {path}")
