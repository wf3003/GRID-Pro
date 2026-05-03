"""网格交易机器人主程序"""
import asyncio
import signal
import sys
import threading
from typing import Dict, List, Optional

from loguru import logger

from src.exchanges.base import ExchangeBase
from src.exchanges.factory import ExchangeFactory
from src.models.config import AppConfig, ExchangeConfig, GridConfig
from src.strategies.grid_strategy import GridStrategy
from src.utils.config_loader import ConfigLoader
from src.utils.risk_manager import RiskManager
from src.web_api import run_web_server


class GridTradingBot:
    """网格交易机器人主控器"""
    
    def __init__(self, config_path: str = "config/config.json"):
        self.config_path = config_path
        self.config: Optional[AppConfig] = None
        self.strategies: Dict[str, GridStrategy] = {}
        self.risk_managers: Dict[str, RiskManager] = {}
        self._running = False
        self._shutdown_event = asyncio.Event()
        
        # 配置日志
        self._setup_logging()
    
    def _setup_logging(self):
        """配置日志系统"""
        logger.remove()  # 移除默认处理器
        logger.add(
            sys.stdout,
            format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level:8}</level> | <cyan>{name}</cyan> | {message}",
            level="INFO",
            colorize=True
        )
        logger.add(
            "logs/grid_bot_{time:YYYY-MM-DD}.log",
            rotation="1 day",
            retention="30 days",
            level="DEBUG",
            format="{time:YYYY-MM-DD HH:mm:ss} | {level:8} | {name} | {message}"
        )
    
    async def initialize(self):
        """初始化机器人"""
        logger.info("=" * 60)
        logger.info("网格交易机器人启动中...")
        logger.info("=" * 60)
        
        # 加载配置
        self.config = ConfigLoader.load_from_file(self.config_path)
        if self.config is None:
            logger.warning(f"无法加载配置: {self.config_path}")
            logger.info("生成默认配置文件...")
            ConfigLoader.save_default_config(self.config_path)
            logger.info(f"请编辑配置文件后重新启动: {self.config_path}")
            return False
        
        logger.info(f"配置加载成功: {len(self.config.exchanges)} 个交易所, "
                    f"{len(self.config.grids)} 个网格策略")
        
        # 初始化交易所和策略
        for grid_config in self.config.grids:
            # 找到对应的交易所配置
            exchange_config = self._find_exchange_config(grid_config)
            if not exchange_config:
                logger.warning(f"未找到网格 {grid_config.symbol} 对应的交易所配置")
                continue
            
            if not exchange_config.enable:
                logger.info(f"交易所 {exchange_config.name} 未启用，跳过")
                continue
            
            # 创建交易所实例
            try:
                exchange = ExchangeFactory.create_exchange(exchange_config)
                logger.info(f"交易所已连接: {exchange_config.name}")
            except Exception as e:
                logger.error(f"创建交易所失败 {exchange_config.name}: {e}")
                continue
            
            # 创建策略
            strategy = GridStrategy(exchange, grid_config)
            key = f"{exchange_config.name}_{grid_config.symbol}"
            self.strategies[key] = strategy
            
            # 创建风控
            risk_manager = RiskManager(exchange, grid_config)
            self.risk_managers[key] = risk_manager
            
            # 初始化策略
            try:
                await strategy.initialize()
                logger.info(f"策略初始化成功: {key}")
            except Exception as e:
                logger.error(f"策略初始化失败 {key}: {e}")
        
        if not self.strategies:
            logger.error("没有可用的策略，请检查配置")
            return False
        
        logger.info(f"初始化完成: {len(self.strategies)} 个策略就绪")
        return True
    
    def _find_exchange_config(self, grid_config: GridConfig) -> Optional[ExchangeConfig]:
        """查找网格策略对应的交易所配置"""
        if not self.config:
            return None
        
        # 简单策略：使用第一个启用的交易所
        for exchange_config in self.config.exchanges:
            if exchange_config.enable:
                return exchange_config
        
        return None
    
    async def start(self):
        """启动所有策略"""
        if not self.strategies:
            logger.error("没有可用的策略，无法启动")
            return
        
        self._running = True
        logger.info(f"启动 {len(self.strategies)} 个网格策略...")
        
        # 启动所有策略
        for key, strategy in self.strategies.items():
            try:
                await strategy.start()
                logger.info(f"策略已启动: {key}")
            except Exception as e:
                logger.error(f"启动策略失败 {key}: {e}")
        
        # 启动状态报告循环
        asyncio.create_task(self._status_report_loop())
        
        logger.info("所有策略已启动，等待交易信号...")
        
        # 等待关闭信号
        await self._shutdown_event.wait()
    
    async def stop(self):
        """停止所有策略"""
        if not self._running:
            return
        
        logger.info("正在停止所有策略...")
        self._running = False
        
        # 停止所有策略
        for key, strategy in self.strategies.items():
            try:
                await strategy.stop()
                logger.info(f"策略已停止: {key}")
            except Exception as e:
                logger.error(f"停止策略失败 {key}: {e}")
        
        # 关闭交易所连接
        await ExchangeFactory.close_all()
        
        self._shutdown_event.set()
        logger.info("所有策略已停止")
    
    async def _status_report_loop(self):
        """状态报告循环"""
        while self._running:
            try:
                await asyncio.sleep(60)  # 每分钟报告一次
                
                if not self._running:
                    break
                
                logger.info("-" * 60)
                logger.info("策略状态报告:")
                
                for key, strategy in self.strategies.items():
                    stats = strategy.get_statistics()
                    logger.info(f"  [{key}]")
                    logger.info(f"    当前价格: {stats['current_price']:.2f}")
                    logger.info(f"    网格范围: {stats['grid_range'][0]:.2f} - {stats['grid_range'][1]:.2f}")
                    logger.info(f"    活跃买单: {stats['active_buy_orders']}")
                    logger.info(f"    活跃卖单: {stats['active_sell_orders']}")
                    logger.info(f"    总交易次数: {stats['total_trades']}")
                    logger.info(f"    总盈亏: {stats['total_pnl']:.8f}")
                
                logger.info("-" * 60)
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"状态报告错误: {e}")
    
    def get_strategy_statistics(self) -> Dict[str, dict]:
        """获取所有策略统计"""
        return {
            key: strategy.get_statistics()
            for key, strategy in self.strategies.items()
        }


def start_web_server_thread():
    """在后台线程中启动 Web 服务器"""
    logger.info("启动 Web 控制台服务器 (http://0.0.0.0:3000)")
    run_web_server(host="0.0.0.0", port=3000)


async def main():
    """主函数"""
    config_path = "config/config.json"
    if len(sys.argv) > 1:
        config_path = sys.argv[1]
    
    # 启动 Web 服务器（后台线程）
    web_thread = threading.Thread(target=start_web_server_thread, daemon=True)
    web_thread.start()
    
    bot = GridTradingBot(config_path)
    
    # 注册信号处理
    loop = asyncio.get_event_loop()
    
    def signal_handler():
        logger.info("收到关闭信号...")
        asyncio.create_task(bot.stop())
    
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, signal_handler)
        except NotImplementedError:
            # Windows 不支持 add_signal_handler
            pass
    
    try:
        initialized = await bot.initialize()
        if initialized:
            await bot.start()
        else:
            logger.error("初始化失败")
    except KeyboardInterrupt:
        logger.info("用户中断")
    finally:
        await bot.stop()


if __name__ == "__main__":
    asyncio.run(main())
