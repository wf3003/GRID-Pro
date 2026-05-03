"""风险管理模块"""
from decimal import Decimal
from typing import Dict, Optional
from datetime import datetime, timedelta

from loguru import logger

from src.exchanges.base import ExchangeBase
from src.models.config import GridConfig
from src.models.trading import Balance, Order, OrderSide, OrderStatus


class RiskManager:
    """风险管理器
    
    功能：
    1. 资金管理 - 确保有足够余额
    2. 止损检查 - 监控亏损情况
    3. 频率限制 - 防止过度交易
    4. 最大持仓限制
    """
    
    def __init__(self, exchange: ExchangeBase, config: GridConfig):
        self.exchange = exchange
        self.config = config
        
        # 交易频率控制
        self._last_trade_time: Dict[str, datetime] = {}
        self._min_trade_interval = timedelta(seconds=10)  # 最小交易间隔
        
        # 每日交易统计
        self._daily_trades = 0
        self._daily_pnl = Decimal("0")
        self._daily_reset_time = datetime.now()
        
        # 止损状态
        self._stop_loss_triggered = False
        self._take_profit_triggered = False
    
    async def check_before_trade(self, side: OrderSide, price: Decimal, quantity: Decimal) -> bool:
        """交易前检查"""
        # 检查止损/止盈
        if self._stop_loss_triggered or self._take_profit_triggered:
            logger.warning("止损/止盈已触发，暂停交易")
            return False
        
        # 检查交易频率
        now = datetime.now()
        last_trade = self._last_trade_time.get(side.value)
        if last_trade and (now - last_trade) < self._min_trade_interval:
            logger.debug(f"交易频率限制: {side.value}")
            return False
        
        # 检查每日交易次数
        self._reset_daily_stats_if_needed()
        if self._daily_trades >= 100:  # 每日最大交易次数
            logger.warning("已达到每日最大交易次数")
            return False
        
        # 检查余额
        if not await self._check_balance(side, price, quantity):
            return False
        
        return True
    
    async def _check_balance(self, side: OrderSide, price: Decimal, quantity: Decimal) -> bool:
        """检查余额是否充足"""
        try:
            if side == OrderSide.BUY:
                # 检查计价货币余额（如 USDT）
                quote_asset = self.config.symbol.split("/")[1]
                balance = await self.exchange.get_balance(quote_asset)
                needed = price * quantity
                
                if balance.free < needed:
                    logger.warning(f"{quote_asset} 余额不足: 需要 {needed}, 可用 {balance.free}")
                    return False
            else:
                # 检查基础货币余额（如 BTC）
                base_asset = self.config.symbol.split("/")[0]
                balance = await self.exchange.get_balance(base_asset)
                
                if balance.free < quantity:
                    logger.warning(f"{base_asset} 余额不足: 需要 {quantity}, 可用 {balance.free}")
                    return False
            
            return True
        except Exception as e:
            logger.error(f"检查余额失败: {e}")
            return False
    
    async def on_trade_completed(self, order: Order):
        """交易完成回调"""
        now = datetime.now()
        self._last_trade_time[order.side.value] = now
        self._daily_trades += 1
        
        # 更新每日盈亏
        if order.side == OrderSide.SELL:
            self._daily_pnl += (order.price * order.filled_quantity)
        
        # 检查止损/止盈
        await self._check_stop_take_profit()
    
    async def _check_stop_take_profit(self):
        """检查止损止盈"""
        # 获取当前持仓和盈亏
        try:
            base_asset = self.config.symbol.split("/")[0]
            quote_asset = self.config.symbol.split("/")[1]
            
            base_balance = await self.exchange.get_balance(base_asset)
            quote_balance = await self.exchange.get_balance(quote_asset)
            
            # 计算总资产价值
            ticker = await self.exchange.get_ticker(self.config.symbol)
            total_value = base_balance.total * ticker.last + quote_balance.total
            
            # 计算初始投资
            initial_investment = self.config.total_investment
            
            # 计算盈亏比例
            if initial_investment > Decimal("0"):
                pnl_ratio = (total_value - initial_investment) / initial_investment
                
                # 检查止损
                if pnl_ratio <= -self.config.stop_loss_pct:
                    logger.warning(f"止损触发! 亏损: {pnl_ratio:.2%}")
                    self._stop_loss_triggered = True
                
                # 检查止盈
                if pnl_ratio >= self.config.take_profit_pct:
                    logger.info(f"止盈触发! 盈利: {pnl_ratio:.2%}")
                    self._take_profit_triggered = True
        
        except Exception as e:
            logger.error(f"检查止损止盈失败: {e}")
    
    def _reset_daily_stats_if_needed(self):
        """重置每日统计"""
        now = datetime.now()
        if now.date() > self._daily_reset_time.date():
            self._daily_trades = 0
            self._daily_pnl = Decimal("0")
            self._daily_reset_time = now
            logger.info("每日统计已重置")
    
    def is_stop_loss_triggered(self) -> bool:
        """是否触发止损"""
        return self._stop_loss_triggered
    
    def is_take_profit_triggered(self) -> bool:
        """是否触发止盈"""
        return self._take_profit_triggered
    
    def reset_stop_conditions(self):
        """重置止损止盈条件"""
        self._stop_loss_triggered = False
        self._take_profit_triggered = False
        logger.info("止损止盈条件已重置")
    
    def get_statistics(self) -> dict:
        """获取风控统计"""
        return {
            "daily_trades": self._daily_trades,
            "daily_pnl": float(self._daily_pnl),
            "stop_loss_triggered": self._stop_loss_triggered,
            "take_profit_triggered": self._take_profit_triggered,
            "min_trade_interval_seconds": self._min_trade_interval.seconds
        }
