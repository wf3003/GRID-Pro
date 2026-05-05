"""GRID-Pro 网格策略核心实现 - 标准单向挂单模式"""
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
    """GRID-Pro 网格策略 - 标准单向挂单模式
    
    核心逻辑：
    1. 当前价格下方挂 N 个买单（只挂买单），上方挂 N 个卖单（只挂卖单）
    2. 买单成交后，在成交价上方一个间隔补一个卖单
    3. 卖单成交后，在成交价下方一个间隔补一个买单
    4. 保留 1 格 USDT 和 1 格币作为缓冲，防止资金耗尽
    5. 价格大幅偏离时整体平移网格
    6. 跌破止损价时自动停止
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
        
        # 网格参数
        self._grid_spacing: Decimal = Decimal("0")  # 当前价格间隔
        self._per_grid_qty: Decimal = Decimal("0")  # 每格数量
        self._per_grid_usdt: Decimal = Decimal("0")  # 每格 USDT 金额
        self._buy_grids: int = 0  # 买单格数
        self._sell_grids: int = 0  # 卖单格数
    
    def _calculate_grid_spacing(self, current_price: Decimal) -> Decimal:
        """计算每格价格间隔"""
        if self.config.price_spacing and self.config.price_spacing > 0:
            spacing_pct = self.config.price_spacing / Decimal("100")
            return current_price * spacing_pct
        # 默认 0.5%
        return current_price * Decimal("0.005")
    
    def _calculate_per_grid_amount(self) -> Decimal:
        """计算每格交易金额（USDT）
        
        注意：此金额是买单和卖单共用的每格 USDT 价值。
        买单每格用 USDT 买入，卖单每格用币卖出。
        所以 per_grid_qty = per_grid_usdt / price，保证买卖对称。
        """
        grid_count = self.config.grid_count
        if grid_count <= 0:
            grid_count = 26
        
        # 总格数 = 买单格数 + 卖单格数（各保留 1 格缓冲）
        half_grids = grid_count // 2
        buy_grids = max(1, half_grids - 1)
        sell_grids = max(1, half_grids - 1)
        total_grids = buy_grids + sell_grids
        
        if self.config.usdt_amount:
            # USDT 分配到所有格子（买单用 USDT，卖单用币）
            return self.config.usdt_amount / Decimal(str(total_grids))
        
        if self.config.total_investment:
            return self.config.total_investment / Decimal(str(total_grids))
        
        return Decimal("10")  # 默认每格 $10
    
    def _calculate_per_grid_quantity(self, price: Decimal) -> Decimal:
        """计算每格交易币数量
        
        保证：per_grid_qty * price = per_grid_usdt
        即买卖对称：买单每格花 per_grid_usdt USDT，卖单每格卖 per_grid_qty 币
        """
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
    
    async def recover_from_existing_orders(self, open_orders: List[Order]) -> bool:
        """从现有挂单恢复网格策略
        
        用于服务重启后，从交易所拉取当前挂单重建网格。
        
        Args:
            open_orders: 交易所当前挂单列表
            
        Returns:
            是否成功恢复
        """
        if not open_orders:
            logger.warning("没有现有挂单，无法恢复")
            return False
        
        logger.info(f"从 {len(open_orders)} 个现有挂单恢复网格")
        
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
        
        # 计算网格参数
        self._grid_spacing = self._calculate_grid_spacing(current_price)
        self._per_grid_qty = self._calculate_per_grid_quantity(current_price)
        self._per_grid_usdt = self._calculate_per_grid_amount()
        
        half_grids = self.config.grid_count // 2
        self._buy_grids = max(1, half_grids - 1)
        self._sell_grids = max(1, half_grids - 1)
        
        # 按价格排序挂单
        buy_orders = sorted(
            [o for o in open_orders if o.side == OrderSide.BUY],
            key=lambda x: x.price,
            reverse=True  # 从高到低
        )
        sell_orders = sorted(
            [o for o in open_orders if o.side == OrderSide.SELL],
            key=lambda x: x.price,
            reverse=False  # 从低到高
        )
        
        self.grid_levels = []
        
        # 重建买单网格
        for i, order in enumerate(buy_orders):
            level = GridLevel(
                level=i + 1,
                buy_price=order.price,
                sell_price=Decimal("0"),
                quantity=order.quantity,
                is_active=True
            )
            level.buy_order = order
            self.grid_levels.append(level)
        
        # 重建卖单网格
        for i, order in enumerate(sell_orders):
            level = GridLevel(
                level=i + 1 + len(buy_orders),
                buy_price=Decimal("0"),
                sell_price=order.price,
                quantity=order.quantity,
                is_active=True
            )
            level.sell_order = order
            self.grid_levels.append(level)
        
        # 按价格排序
        self.grid_levels.sort(key=lambda x: x.buy_price if x.buy_price > 0 else x.sell_price)
        
        # 更新统计
        self.status.active_buy_orders = len(buy_orders)
        self.status.active_sell_orders = len(sell_orders)
        
        logger.info(f"网格恢复完成: {self.status.active_buy_orders} 买单, "
                    f"{self.status.active_sell_orders} 卖单")
        return True
    
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
        
        # 先取消所有挂单（在取消任务之前执行，确保 API 调用正常）
        await self._cancel_all_orders()
        
        # 再取消所有任务
        for task in self._tasks:
            task.cancel()
        
        # 等待任务真正结束
        if self._tasks:
            await asyncio.sleep(0.5)
        
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
        """处理订单 - 检查成交并补单
        
        核心逻辑：
        - 买单成交 → 在成交价上方一个间隔补一个卖单，记录成交
        - 卖单成交 → 在成交价下方一个间隔补一个买单，记录成交
        - 每3秒扫描一次，确保网格始终完整
        """
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
        open_order_ids = {o.id for o in open_orders if o.id}
        
        # 检查每个网格层的订单状态
        for level in self.grid_levels:
            if not level.is_active:
                continue
            
            # 检查买单是否成交
            if level.buy_order and level.buy_order.id:
                if level.buy_order.id not in open_order_ids:
                    # 订单不在挂单列表中，说明已被吃掉或取消
                    is_filled = False
                    filled_qty = level.buy_order.quantity
                    try:
                        filled_order = await self.exchange.get_order(
                            self.config.symbol, level.buy_order.id
                        )
                        if filled_order and filled_order.status == OrderStatus.FILLED:
                            is_filled = True
                            filled_qty = filled_order.filled_quantity
                        elif filled_order is None:
                            # Gate.io 查询已成交订单可能返回 None（404），视为已成交
                            is_filled = True
                            logger.info(f"买单 {level.buy_order.id} 不在挂单列表且查询不到，视为已成交")
                    except Exception as e:
                        logger.error(f"检查买单 {level.buy_order.id} 状态失败: {e}")
                        # 查询失败也视为已成交，避免卡住
                        is_filled = True
                    
                    if is_filled:
                        # 记录成交
                        filled_price = level.buy_price
                        filled_amount = filled_price * filled_qty
                        fee = float(filled_amount * self.config.fee_rate / Decimal("2"))
                        
                        # 创建成交订单对象并通知
                        filled_order_obj = Order(
                            id=level.buy_order.id,
                            exchange=self.exchange.name,
                            symbol=self.config.symbol,
                            side=OrderSide.BUY,
                            price=filled_price,
                            quantity=filled_qty,
                            filled_quantity=filled_qty,
                            status=OrderStatus.FILLED,
                            type=OrderType.LIMIT
                        )
                        await self._on_order_filled(filled_order_obj)
                        
                        level.buy_order = None
                        # 买单成交 → 在成交价上方一个间隔补一个卖单
                        sell_price = level.buy_price + self._grid_spacing
                        level.sell_price = self._round_price(sell_price)
                        ok = await self._place_sell_order(level)
                        if ok:
                            logger.info(f"买单已成交，补卖单成功: {level.sell_price} x {level.quantity}")
                        else:
                            logger.warning(f"买单已成交，但补卖单失败: {level.sell_price} x {level.quantity}")
            
            # 检查卖单是否成交
            if level.sell_order and level.sell_order.id:
                if level.sell_order.id not in open_order_ids:
                    # 订单不在挂单列表中，说明已被吃掉或取消
                    is_filled = False
                    filled_qty = level.sell_order.quantity
                    try:
                        filled_order = await self.exchange.get_order(
                            self.config.symbol, level.sell_order.id
                        )
                        if filled_order and filled_order.status == OrderStatus.FILLED:
                            is_filled = True
                            filled_qty = filled_order.filled_quantity
                        elif filled_order is None:
                            # Gate.io 查询已成交订单可能返回 None（404），视为已成交
                            is_filled = True
                            logger.info(f"卖单 {level.sell_order.id} 不在挂单列表且查询不到，视为已成交")
                    except Exception as e:
                        logger.error(f"检查卖单 {level.sell_order.id} 状态失败: {e}")
                        # 查询失败也视为已成交，避免卡住
                        is_filled = True
                    
                    if is_filled:
                        # 记录成交
                        filled_price = level.sell_price
                        filled_amount = filled_price * filled_qty
                        fee = float(filled_amount * self.config.fee_rate / Decimal("2"))
                        
                        # 创建成交订单对象并通知
                        filled_order_obj = Order(
                            id=level.sell_order.id,
                            exchange=self.exchange.name,
                            symbol=self.config.symbol,
                            side=OrderSide.SELL,
                            price=filled_price,
                            quantity=filled_qty,
                            filled_quantity=filled_qty,
                            status=OrderStatus.FILLED,
                            type=OrderType.LIMIT
                        )
                        await self._on_order_filled(filled_order_obj)
                        
                        level.sell_order = None
                        # 卖单成交 → 在成交价下方一个间隔补一个买单
                        buy_price = level.sell_price - self._grid_spacing
                        level.buy_price = self._round_price(buy_price)
                        ok = await self._place_buy_order(level)
                        if ok:
                            logger.info(f"卖单已成交，补买单成功: {level.buy_price} x {level.quantity}")
                        else:
                            logger.warning(f"卖单已成交，但补买单失败: {level.buy_price} x {level.quantity}")
        
        # 检查是否有网格层缺少订单（比如补单失败的情况）
        # 如果某个活跃层既没有买单也没有卖单，尝试重新挂单
        for level in self.grid_levels:
            if not level.is_active:
                continue
            if not level.buy_order and not level.sell_order:
                # 这个层没有订单了，根据价格位置决定挂买单还是卖单
                if level.buy_price > 0 and level.sell_price > 0:
                    # 两个价格都有，看哪个更接近当前价格
                    if abs(level.buy_price - current_price) < abs(level.sell_price - current_price):
                        ok = await self._place_buy_order(level)
                        if ok:
                            logger.info(f"补挂买单: {level.buy_price} x {level.quantity}")
                    else:
                        ok = await self._place_sell_order(level)
                        if ok:
                            logger.info(f"补挂卖单: {level.sell_price} x {level.quantity}")
                elif level.buy_price > 0:
                    ok = await self._place_buy_order(level)
                    if ok:
                        logger.info(f"补挂买单: {level.buy_price} x {level.quantity}")
                elif level.sell_price > 0:
                    ok = await self._place_sell_order(level)
                    if ok:
                        logger.info(f"补挂卖单: {level.sell_price} x {level.quantity}")
        
        # 更新活跃订单统计
        active_buys = 0
        active_sells = 0
        for l in self.grid_levels:
            if l.buy_order:
                active_buys += 1
            if l.sell_order:
                active_sells += 1
        self.status.active_buy_orders = active_buys
        self.status.active_sell_orders = active_sells
    
    async def _check_min_order_amount(self, current_price: Decimal) -> Decimal:
        """检查并返回满足交易所最小订单金额的每格 USDT 金额
        
        如果当前计算的每格金额小于交易所最小要求，则自动调整。
        """
        try:
            min_amount = await self.exchange.get_min_order_amount(self.config.symbol)
        except Exception:
            min_amount = Decimal("0")
        
        if min_amount <= Decimal("0"):
            return self._per_grid_usdt
        
        # 检查每格金额是否满足最小要求
        if self._per_grid_usdt < min_amount:
            logger.warning(f"每格金额 {self._per_grid_usdt:.2f} USDT 小于交易所最小要求 {min_amount:.2f} USDT")
            
            # 方案1：尝试减少网格数量来增加每格金额
            total_usdt = self._per_grid_usdt * Decimal(str(self._buy_grids + self._sell_grids))
            max_grids = int(total_usdt / min_amount)
            
            if max_grids >= 2:
                # 重新计算网格数量
                new_half = max(1, max_grids // 2)
                self._buy_grids = max(1, new_half - 1)
                self._sell_grids = max(1, new_half - 1)
                total_grids = self._buy_grids + self._sell_grids
                self._per_grid_usdt = total_usdt / Decimal(str(total_grids))
                logger.info(f"已自动调整: 网格数={total_grids}, 每格={self._per_grid_usdt:.2f} USDT")
            else:
                # 方案2：直接使用最小金额
                self._per_grid_usdt = min_amount
                logger.info(f"已自动调整: 每格金额设为最小值 {min_amount:.2f} USDT")
            
            # 重新计算每格币数量
            self._per_grid_qty = self._calculate_per_grid_quantity(current_price)
        
        return self._per_grid_usdt
    
    async def _place_initial_orders(self):
        """放置初始网格订单
        
        单向挂单模式：
        - 当前价格下方挂 N 个买单（只挂买单）
        - 当前价格上方挂 N 个卖单（只挂卖单）
        
        注意：挂单前会先取消所有现有挂单，避免重复
        """
        # 先取消所有现有挂单，避免重复
        try:
            existing_orders = await self.exchange.get_open_orders(self.config.symbol)
            if existing_orders:
                logger.info(f"检测到 {len(existing_orders)} 个现有挂单，先取消")
                for order in existing_orders:
                    if order and hasattr(order, 'id') and order.id:
                        try:
                            await self.exchange.cancel_order(self.config.symbol, order.id)
                        except Exception as e:
                            logger.warning(f"取消订单 {order.id} 失败: {e}")
                await asyncio.sleep(1)  # 等待取消完成
        except Exception as e:
            logger.warning(f"取消现有挂单时出错: {e}")
        
        ticker = await self.exchange.get_ticker(self.config.symbol)
        current_price = ticker.last
        
        # 计算网格参数
        self._grid_spacing = self._calculate_grid_spacing(current_price)
        self._per_grid_qty = self._calculate_per_grid_quantity(current_price)
        self._per_grid_usdt = self._calculate_per_grid_amount()
        
        half_grids = self.config.grid_count // 2
        
        # 保留 1 格缓冲
        self._buy_grids = max(1, half_grids - 1)
        self._sell_grids = max(1, half_grids - 1)
        
        # 检查并调整最小订单金额
        await self._check_min_order_amount(current_price)
        
        # 检查余额，如果币不够则自动买入
        base_asset = self.config.symbol.split('/')[0]
        usdt_balance = await self.exchange.get_balance("USDT")
        coin_balance = await self.exchange.get_balance(base_asset)
        
        # 计算需要的币数量（卖单需要的币 + 1格缓冲）
        need_coin = self._per_grid_qty * Decimal(str(self._sell_grids + 1))
        # 计算需要的 USDT 数量（买单需要的 USDT + 1格缓冲）
        need_usdt = self._per_grid_usdt * Decimal(str(self._buy_grids + 1))
        
        logger.info(f"余额检查: USDT={float(usdt_balance.free):.2f}, {base_asset}={float(coin_balance.free):.6f}")
        logger.info(f"需求: USDT={float(need_usdt):.2f}, {base_asset}={float(need_coin):.6f}")
        
        # 如果币不够，用 USDT 买入
        if coin_balance.free < need_coin and usdt_balance.free > need_usdt:
            buy_coin_amount = need_coin - coin_balance.free
            buy_usdt_cost = buy_coin_amount * current_price
            # 加 0.5% 滑点
            buy_price = current_price * Decimal("1.005")
            
            logger.info(f"币不足，需要买入 {float(buy_coin_amount):.6f} {base_asset}，"
                        f"预计花费 ${float(buy_usdt_cost):.2f}")
            
            try:
                # 用限价单买入
                order = await self.exchange.create_limit_order(
                    symbol=self.config.symbol,
                    side=OrderSide.BUY,
                    price=self._round_price(buy_price),
                    quantity=self._round_quantity(buy_coin_amount)
                )
                logger.info(f"已买入 {float(buy_coin_amount):.6f} {base_asset}")
                # 等待成交
                await asyncio.sleep(2)
            except Exception as e:
                logger.error(f"自动买入币失败: {e}")
        
        self.grid_levels = []
        
        # 创建买单网格（低于当前价，只挂买单）
        for i in range(1, self._buy_grids + 1):
            buy_price = current_price - (self._grid_spacing * Decimal(str(i)))
            if buy_price <= Decimal("0"):
                continue
            
            level = GridLevel(
                level=i,
                buy_price=self._round_price(buy_price),
                sell_price=Decimal("0"),  # 初始没有卖单
                quantity=self._per_grid_qty,
                is_active=False
            )
            self.grid_levels.append(level)
        
        # 创建卖单网格（高于当前价，只挂卖单）
        for i in range(1, self._sell_grids + 1):
            sell_price = current_price + (self._grid_spacing * Decimal(str(i)))
            
            level = GridLevel(
                level=i + self._buy_grids,
                buy_price=Decimal("0"),  # 初始没有买单
                sell_price=self._round_price(sell_price),
                quantity=self._per_grid_qty,
                is_active=False
            )
            self.grid_levels.append(level)
        
        # 按价格排序
        self.grid_levels.sort(key=lambda x: x.buy_price if x.buy_price > 0 else x.sell_price)
        
        # 挂买单（只挂买单）
        buy_success = 0
        buy_fail = 0
        for level in self.grid_levels:
            if level.buy_price > 0:
                ok = await self._place_buy_order(level)
                if ok:
                    level.is_active = True
                    buy_success += 1
                else:
                    buy_fail += 1
        
        # 挂卖单（只挂卖单）
        sell_success = 0
        sell_fail = 0
        for level in self.grid_levels:
            if level.sell_price > 0:
                ok = await self._place_sell_order(level)
                if ok:
                    level.is_active = True
                    sell_success += 1
                else:
                    sell_fail += 1
        
        logger.info(f"初始订单已放置: 买单 {buy_success}成功/{buy_fail}失败, "
                    f"卖单 {sell_success}成功/{sell_fail}失败")
        if buy_fail > 0 or sell_fail > 0:
            logger.warning(f"部分订单挂单失败！请检查 API Key 权限和账户余额")
    
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
                if order and hasattr(order, 'id') and order.id:
                    try:
                        await self.exchange.cancel_order(self.config.symbol, order.id)
                    except Exception as e:
                        logger.warning(f"取消订单 {order.id} 失败: {e}")
            
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
        
        buy_prices = [l.buy_price for l in self.grid_levels if l.buy_price and l.buy_price > 0]
        sell_prices = [l.sell_price for l in self.grid_levels if l.sell_price and l.sell_price > 0]
        
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
        new_buy_prices = [l.buy_price for l in self.grid_levels if l.buy_price and l.buy_price > 0]
        new_sell_prices = [l.sell_price for l in self.grid_levels if l.sell_price and l.sell_price > 0]
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
            buy_prices = [l.buy_price for l in self.grid_levels if l.buy_price and l.buy_price > 0]
            sell_prices = [l.sell_price for l in self.grid_levels if l.sell_price and l.sell_price > 0]
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
