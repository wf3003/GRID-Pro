"""网格交易策略核心实现 - 动态跟随模式"""
import asyncio
from decimal import Decimal, ROUND_DOWN
from typing import Dict, List, Optional, Tuple, Callable
from datetime import datetime

from loguru import logger

from src.exchanges.base import ExchangeBase
from src.models.config import GridConfig
from src.models.trading import (
    GridLevel, GridStatus, Order, OrderSide, OrderStatus, Ticker
)


class GridStrategy:
    """网格交易策略 - 动态跟随模式
    
    核心逻辑：
    1. 以当前价格为中心，上下各挂 N 格订单
    2. 不设固定上下限，价格移动时整体平移网格
    3. 成交后自动在更远位置补单
    4. 保留 1 格 USDT 和 1 格币作为缓冲，防止资金耗尽
    5. 跌破止损价时自动停止
    """
    
    def __init__(self, exchange: ExchangeBase, config: GridConfig):
        self.exchange = exchange
        self.config = config
        self.grid_levels: List[GridLevel] = []
        self.status = GridStatus(
            symbol=config.symbol,
            exchange=exchange.name
        )
        self._running = False
        self._tasks: List[asyncio.Task] = []
        
        # 精度缓存
        self._price_precision: int = 8
        self._qty_precision: int = 8
        
        # 成交记录
        self._filled_orders: List[Order] = []
        
        # 回调函数
        self.on_order_placed: Optional[Callable] = None
        self.on_order_filled_callback: Optional[Callable] = None
        self.on_trade_callback: Optional[Callable] = None
        self.on_grid_adjusted: Optional[Callable] = None
        self.on_status_update: Optional[Callable] = None
        
        # 网格配置ID（数据库）
        self.db_config_id: Optional[int] = None
        
        # 资金跟踪
        self._usdt_reserved: Decimal = Decimal("0")  # 已预留的 USDT
        self._coin_reserved: Decimal = Decimal("0")  # 已预留的币
    
    def _calculate_grid_spacing(self, current_price: Decimal) -> Decimal:
        """计算每格价格间隔"""
        if self.config.price_spacing and self.config.price_spacing > 0:
            spacing_pct = self.config.price_spacing / Decimal("100")
            return current_price * spacing_pct
        # 默认 0.5%
        return current_price * Decimal("0.005")
    
    def _calculate_per_grid_amount(self) -> Decimal:
        """计算每格交易金额（USDT）"""
        grid_count = self.config.grid_count
        if grid_count <= 0:
            grid_count = 26
        
        # 买单格数 = 总格数 / 2（保留 1 格缓冲）
        buy_grids = max(1, grid_count // 2 - 1)
        
        if self.config.usdt_amount:
            return self.config.usdt_amount / Decimal(str(buy_grids))
        
        if self.config.total_investment:
            return self.config.total_investment / Decimal(str(buy_grids))
        
        return Decimal("10")  # 默认每格 $10
    
    def _calculate_per_grid_quantity(self, price: Decimal) -> Decimal:
        """计算每格交易币数量"""
        if self.config.coin_amount:
            sell_grids = max(1, self.config.grid_count // 2 - 1)
            return self.config.coin_amount / Decimal(str(sell_grids))
        
        if self.config.base_quantity:
            return self._round_quantity(self.config.base_quantity)
        
        # 根据每格 USDT 金额计算
        per_grid_usdt = self._calculate_per_grid_amount()
        quantity = per_grid_usdt / price
        return self._round_quantity(quantity)
    
    def _calculate_min_required(self, current_price: Decimal) -> dict:
        """计算最少需要的 USDT 和币量"""
        grid_count = self.config.grid_count
        if grid_count <= 0:
            grid_count = 26
        
        # 买单格数（保留 1 格缓冲）
        buy_grids = max(1, grid_count // 2 - 1)
        # 卖单格数（保留 1 格缓冲）
        sell_grids = max(1, grid_count // 2 - 1)
        
        per_grid_usdt = self._calculate_per_grid_amount()
        per_grid_qty = self._calculate_per_grid_quantity(current_price)
        
        min_usdt = per_grid_usdt * Decimal(str(buy_grids))
        min_coin = per_grid_qty * Decimal(str(sell_grids))
        
        return {
            "buy_grids": buy_grids,
            "sell_grids": sell_grids,
            "per_grid_usdt": per_grid_usdt,
            "per_grid_qty": per_grid_qty,
            "min_usdt": min_usdt,
            "min_coin": min_coin,
            "total_grids": buy_grids + sell_grids
        }
    
    def _round_price(self, price: Decimal) -> Decimal:
        """价格精度处理"""
        precision = Decimal(str(10 ** -self._price_precision))
        return price.quantize(precision, rounding=ROUND_DOWN)
    
    def _round_quantity(self, quantity: Decimal) -> Decimal:
        """数量精度处理"""
        if quantity <= Decimal("0"):
            return Decimal("0")
        precision = Decimal(str(10 ** -self._qty_precision))
        return quantity.quantize(precision, rounding=ROUND_DOWN)
    
    async def initialize(self):
        """初始化网格策略"""
        logger.info(f"初始化网格策略: {self.config.symbol} @ {self.exchange.name}")
        
        # 获取精度信息
        try:
            self._price_precision, self._qty_precision = \
                await self.exchange.get_symbol_precision(self.config.symbol)
        except Exception as e:
            logger.warning(f"获取精度信息失败，使用默认值: {e}")
        
        # 获取当前价格
        ticker = await self.exchange.get_ticker(self.config.symbol)
        current_price = ticker.last
        
        self.status.current_price = current_price
        
        # 计算资金需求
        funds = self._calculate_min_required(current_price)
        logger.info(f"资金需求: USDT={funds['min_usdt']:.2f}, 币={funds['min_coin']:.6f}, "
                    f"每格USDT={funds['per_grid_usdt']:.2f}, 每格币={funds['per_grid_qty']:.6f}")
        
        logger.info(f"网格已初始化: {funds['total_grids']} 格 "
                    f"({funds['buy_grids']}买 + {funds['sell_grids']}卖)")
    
    async def start(self):
        """启动网格策略"""
        if self._running:
            logger.warning("网格策略已在运行")
            return
        
        self._running = True
        self.status.is_running = True
        
        logger.info(f"启动网格策略: {self.config.symbol}")
        
        # 放置初始订单
        await self._place_initial_orders()
        
        # 启动主循环
        task = asyncio.create_task(self._main_loop())
        self._tasks.append(task)
        
        # 启动调整循环
        adjust_task = asyncio.create_task(self._auto_adjust_loop())
        self._tasks.append(adjust_task)
    
    async def stop(self):
        """停止网格策略"""
        self._running = False
        self.status.is_running = False
        
        # 取消所有任务
        for task in self._tasks:
            task.cancel()
        
        # 取消所有挂单
        await self._cancel_all_orders()
        
        logger.info(f"网格策略已停止: {self.config.symbol}")
    
    async def _main_loop(self):
        """主循环 - 持续监控和处理订单"""
        while self._running:
            try:
                await self._process_orders()
                await self._notify_status()
                await asyncio.sleep(3)  # 每3秒检查一次
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"主循环错误: {e}")
                await asyncio.sleep(10)
    
    async def _process_orders(self):
        """处理订单 - 检查成交并补单"""
        # 获取当前行情
        ticker = await self.exchange.get_ticker(self.config.symbol)
        current_price = ticker.last
        self.status.current_price = current_price
        
        # 检查止损
        if self.config.stop_loss_price and current_price <= self.config.stop_loss_price:
            logger.warning(f"价格 {current_price} 跌破止损线 {self.config.stop_loss_price}，停止策略")
            await self.stop()
            return
        
        # 获取当前挂单
        open_orders = await self.exchange.get_open_orders(self.config.symbol)
        open_order_ids = {o.id for o in open_orders}
        
        # 检查每个网格层的订单状态
        for level in self.grid_levels:
            if not level.is_active:
                continue
            
            # 检查买单是否成交
            if level.buy_order and level.buy_order.id not in open_order_ids:
                filled_order = await self.exchange.get_order(
                    self.config.symbol, level.buy_order.id
                )
                if filled_order and filled_order.status == OrderStatus.FILLED:
                    await self._on_order_filled(filled_order)
                    level.buy_order = None
                    # 在更低价格重新挂买单
                    await self._place_buy_order(level)
            
            # 检查卖单是否成交
            if level.sell_order and level.sell_order.id not in open_order_ids:
                filled_order = await self.exchange.get_order(
                    self.config.symbol, level.sell_order.id
                )
                if filled_order and filled_order.status == OrderStatus.FILLED:
                    await self._on_order_filled(filled_order)
                    level.sell_order = None
                    # 在更高价格重新挂卖单
                    await self._place_sell_order(level)
        
        # 更新活跃订单统计
        self.status.active_buy_orders = sum(
            1 for l in self.grid_levels 
            if l.buy_order and l.buy_order.status == OrderStatus.OPEN
        )
        self.status.active_sell_orders = sum(
            1 for l in self.grid_levels 
            if l.sell_order and l.sell_order.status == OrderStatus.OPEN
        )
    
    async def _place_initial_orders(self):
        """放置初始网格订单"""
        ticker = await self.exchange.get_ticker(self.config.symbol)
        current_price = ticker.last
        
        # 计算网格参数
        grid_spacing = self._calculate_grid_spacing(current_price)
        per_grid_qty = self._calculate_per_grid_quantity(current_price)
        half_grids = self.config.grid_count // 2
        
        # 保留 1 格缓冲
        buy_grids = max(1, half_grids - 1)
        sell_grids = max(1, half_grids - 1)
        
        self.grid_levels = []
        
        # 创建买单网格（低于当前价）
        for i in range(1, buy_grids + 1):
            buy_price = current_price - (grid_spacing * Decimal(str(i)))
            if buy_price <= Decimal("0"):
                continue
            
            sell_price = buy_price + grid_spacing
            
            level = GridLevel(
                level=i,
                buy_price=self._round_price(buy_price),
                sell_price=self._round_price(sell_price),
                quantity=per_grid_qty,
                is_active=False
            )
            self.grid_levels.append(level)
        
        # 创建卖单网格（高于当前价）
        for i in range(1, sell_grids + 1):
            sell_price = current_price + (grid_spacing * Decimal(str(i)))
            buy_price = sell_price - grid_spacing
            
            level = GridLevel(
                level=i + buy_grids,
                buy_price=self._round_price(buy_price),
                sell_price=self._round_price(sell_price),
                quantity=per_grid_qty,
                is_active=False
            )
            self.grid_levels.append(level)
        
        # 按价格排序
        self.grid_levels.sort(key=lambda x: x.buy_price)
        
        # 挂买单
        for level in self.grid_levels:
            await self._place_buy_order(level)
            level.is_active = True
        
        # 挂卖单
        for level in self.grid_levels:
            await self._place_sell_order(level)
            level.is_active = True
        
        logger.info(f"初始订单已放置: {self.status.active_buy_orders} 买单, "
                    f"{self.status.active_sell_orders} 卖单")
    
    async def _place_buy_order(self, level: GridLevel) -> bool:
        """在指定层级放置买单"""
        try:
            order = await self.exchange.create_limit_order(
                symbol=self.config.symbol,
                side=OrderSide.BUY,
                price=level.buy_price,
                quantity=level.quantity
            )
            level.buy_order = order
            level.is_active = True
            
            # 通知回调
            if self.on_order_placed:
                self.on_order_placed({
                    'exchange_order_id': order.id,
                    'symbol': self.config.symbol,
                    'side': 'buy',
                    'price': float(level.buy_price),
                    'quantity': float(level.quantity),
                    'amount': float(level.buy_price * level.quantity),
                    'grid_level': level.level
                })
            
            logger.debug(f"放置买单: {level.buy_price} x {level.quantity}")
            return True
        except Exception as e:
            logger.error(f"放置买单失败: {e}")
            return False
    
    async def _place_sell_order(self, level: GridLevel) -> bool:
        """在指定层级放置卖单"""
        try:
            order = await self.exchange.create_limit_order(
                symbol=self.config.symbol,
                side=OrderSide.SELL,
                price=level.sell_price,
                quantity=level.quantity
            )
            level.sell_order = order
            level.is_active = True
            
            # 通知回调
            if self.on_order_placed:
                self.on_order_placed({
                    'exchange_order_id': order.id,
                    'symbol': self.config.symbol,
                    'side': 'sell',
                    'price': float(level.sell_price),
                    'quantity': float(level.quantity),
                    'amount': float(level.sell_price * level.quantity),
                    'grid_level': level.level
                })
            
            logger.debug(f"放置卖单: {level.sell_price} x {level.quantity}")
            return True
        except Exception as e:
            logger.error(f"放置卖单失败: {e}")
            return False
    
    async def _on_order_filled(self, order: Order):
        """订单成交回调"""
        self._filled_orders.append(order)
        self.status.total_trades += 1
        
        # 计算手续费
        fee = float(order.price * order.filled_quantity * self.config.fee_rate / Decimal("2"))
        self.status.total_fees += Decimal(str(fee))
        
        # 计算盈亏
        profit = 0.0
        if order.side == OrderSide.SELL:
            # 找到对应的买入订单计算利润
            buy_order = next(
                (o for o in reversed(self._filled_orders) 
                 if o.side == OrderSide.BUY and o.price < order.price),
                None
            )
            if buy_order:
                gross_profit = (order.price - buy_order.price) * order.filled_quantity
                total_fee = float(order.price * order.filled_quantity * self.config.fee_rate)
                profit = float(gross_profit) - total_fee
                self.status.total_pnl += Decimal(str(profit))
                logger.info(f"网格套利成功! 毛利: {gross_profit:.8f}, 净利润: {profit:.8f}")
        
        # 通知成交回调
        if self.on_order_filled_callback:
            self.on_order_filled_callback({
                'exchange_order_id': order.id,
                'symbol': self.config.symbol,
                'side': order.side.value,
                'price': float(order.price),
                'quantity': float(order.filled_quantity),
                'amount': float(order.price * order.filled_quantity),
                'fee': fee,
                'profit': profit
            })
        
        logger.info(f"订单成交: {order.side.value.upper()} "
                    f"{order.price} x {order.filled_quantity}")
    
    async def _cancel_all_orders(self):
        """取消所有挂单"""
        try:
            open_orders = await self.exchange.get_open_orders(self.config.symbol)
            for order in open_orders:
                await self.exchange.cancel_order(self.config.symbol, order.id)
            
            # 清除网格层级的订单引用
            for level in self.grid_levels:
                level.buy_order = None
                level.sell_order = None
                level.is_active = False
            
            logger.info(f"已取消所有挂单: {len(open_orders)} 个")
        except Exception as e:
            logger.error(f"取消挂单失败: {e}")
    
    async def _auto_adjust_loop(self):
        """自动调整循环 - 根据行情移动网格"""
        while self._running:
            try:
                await asyncio.sleep(self.config.adjust_interval)
                await self._adjust_grid()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"自动调整错误: {e}")
    
    async def _adjust_grid(self):
        """调整网格位置 - 跟随价格移动"""
        ticker = await self.exchange.get_ticker(self.config.symbol)
        current_price = ticker.last
        
        # 计算当前网格中心
        if not self.grid_levels:
            return
        
        buy_prices = [l.buy_price for l in self.grid_levels if l.buy_price]
        sell_prices = [l.sell_price for l in self.grid_levels if l.sell_price]
        
        if not buy_prices or not sell_prices:
            return
        
        grid_center = (min(sell_prices) + max(buy_prices)) / Decimal("2")
        
        # 计算偏离比例
        deviation = abs(current_price - grid_center) / grid_center
        
        if deviation < self.config.price_deviation_threshold:
            logger.debug(f"偏离 {deviation:.4%} 在阈值内，无需调整")
            return
        
        logger.info(f"价格偏离 {deviation:.4%} 超过阈值 {self.config.price_deviation_threshold}，调整网格")
        
        old_range = [float(min(buy_prices)), float(max(sell_prices))]
        
        # 取消所有现有订单
        await self._cancel_all_orders()
        
        # 重新放置订单
        await self._place_initial_orders()
        
        self.status.last_adjust_time = datetime.now()
        
        # 计算新范围
        new_buy_prices = [l.buy_price for l in self.grid_levels if l.buy_price]
        new_sell_prices = [l.sell_price for l in self.grid_levels if l.sell_price]
        new_range = [float(min(new_buy_prices)), float(max(new_sell_prices))]
        
        # 通知调整回调
        if self.on_grid_adjusted:
            self.on_grid_adjusted({
                'old_range': old_range,
                'new_range': new_range,
                'current_price': float(current_price)
            })
        
        logger.info(f"网格调整完成: [{old_range[0]:.2f}, {old_range[1]:.2f}] -> "
                    f"[{new_range[0]:.2f}, {new_range[1]:.2f}]")
    
    async def _notify_status(self):
        """通知状态更新"""
        if self.on_status_update:
            self.on_status_update(self.get_statistics())
    
    async def get_status(self) -> GridStatus:
        """获取网格状态"""
        self.status.grid_levels = self.grid_levels
        return self.status
    
    def get_statistics(self) -> dict:
        """获取交易统计"""
        # 计算当前网格范围
        if self.grid_levels:
            buy_prices = [l.buy_price for l in self.grid_levels if l.buy_price]
            sell_prices = [l.sell_price for l in self.grid_levels if l.sell_price]
            grid_range = [
                float(min(buy_prices)) if buy_prices else 0,
                float(max(sell_prices)) if sell_prices else 0
            ]
        else:
            grid_range = [0, 0]
        
        return {
            "symbol": self.config.symbol,
            "exchange": self.exchange.name,
            "total_trades": self.status.total_trades,
            "total_pnl": float(self.status.total_pnl),
            "total_fees": float(self.status.total_fees),
            "active_buy_orders": self.status.active_buy_orders,
            "active_sell_orders": self.status.active_sell_orders,
            "grid_range": grid_range,
            "grid_count": len(self.grid_levels),
            "current_price": float(self.status.current_price) if self.status.current_price else 0,
            "is_running": self._running,
            "profit_per_trade": float(self.config.profit_per_trade),
            "fee_rate": float(self.config.fee_rate),
            "price_spacing": float(self.config.price_spacing) if self.config.price_spacing else 0.5
        }
