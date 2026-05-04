"""数据库管理模块 - SQLite 持久化存储"""
import sqlite3
import json
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Dict, Any
from pathlib import Path

from loguru import logger


class Database:
    """SQLite 数据库管理器"""
    
    def __init__(self, db_path: str = "data/grid_bot.db"):
        self.db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_db()
    
    def _get_connection(self) -> sqlite3.Connection:
        """获取数据库连接"""
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    
    def _init_db(self):
        """初始化数据库表"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            
            # 用户表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    username TEXT UNIQUE NOT NULL,
                    password_hash TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 网格策略配置表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS grid_configs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    exchange TEXT NOT NULL,
                    symbol TEXT NOT NULL,
                    upper_price REAL,
                    lower_price REAL,
                    grid_count INTEGER NOT NULL DEFAULT 0,
                    total_investment REAL,
                    base_quantity REAL,
                    profit_per_trade REAL DEFAULT 0.01,
                    fee_rate REAL DEFAULT 0.002,
                    price_spacing REAL,
                    status TEXT DEFAULT 'stopped',
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # 检查是否需要添加 price_spacing 列（兼容旧数据库）
            try:
                cursor.execute("ALTER TABLE grid_configs ADD COLUMN price_spacing REAL")
            except sqlite3.OperationalError:
                pass  # 列已存在
            
            # 挂单记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS orders (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    grid_config_id INTEGER,
                    exchange_order_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    amount REAL NOT NULL,
                    status TEXT NOT NULL DEFAULT 'open',
                    grid_level INTEGER,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (grid_config_id) REFERENCES grid_configs(id)
                )
            """)
            
            # 成交记录表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS trades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    grid_config_id INTEGER,
                    exchange_order_id TEXT,
                    symbol TEXT NOT NULL,
                    side TEXT NOT NULL,
                    price REAL NOT NULL,
                    quantity REAL NOT NULL,
                    amount REAL NOT NULL,
                    fee REAL DEFAULT 0,
                    profit REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (grid_config_id) REFERENCES grid_configs(id)
                )
            """)
            
            # 账户余额快照表
            cursor.execute("""
                CREATE TABLE IF NOT EXISTS balance_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    grid_config_id INTEGER,
                    usdt_balance REAL NOT NULL,
                    coin_balance REAL NOT NULL,
                    coin_price REAL NOT NULL,
                    total_value REAL NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (grid_config_id) REFERENCES grid_configs(id)
                )
            """)
            
            conn.commit()
            logger.info(f"数据库初始化完成: {self.db_path}")
        finally:
            conn.close()
    
    # ========== 网格配置 ==========
    
    def save_grid_config(self, exchange: str, symbol: str,
                         grid_count: int = 26,
                         price_spacing: Optional[float] = None,
                         usdt_amount: Optional[float] = None,
                         coin_amount: Optional[float] = None,
                         stop_loss_price: Optional[float] = None,
                         total_investment: Optional[float] = None,
                         base_quantity: Optional[float] = None,
                         profit_per_trade: float = 0.01,
                         fee_rate: float = 0.002,
                         upper_price: Optional[float] = None,
                         lower_price: Optional[float] = None) -> int:
        """保存网格配置，返回配置ID"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO grid_configs 
                (exchange, symbol, upper_price, lower_price, grid_count,
                 total_investment, base_quantity, profit_per_trade, fee_rate,
                 price_spacing)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (exchange, symbol, upper_price, lower_price, grid_count,
                  total_investment, base_quantity, profit_per_trade, fee_rate,
                  price_spacing))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()
    
    def update_grid_status(self, config_id: int, status: str):
        """更新网格策略状态"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE grid_configs SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, config_id))
            conn.commit()
        finally:
            conn.close()
    
    def get_grid_configs(self) -> List[Dict[str, Any]]:
        """获取所有网格配置"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM grid_configs ORDER BY created_at DESC")
            rows = cursor.fetchall()
            return [dict(row) for row in rows]
        finally:
            conn.close()
    
    def get_active_grid_config(self) -> Optional[Dict[str, Any]]:
        """获取当前运行的网格配置"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM grid_configs WHERE status = 'running' LIMIT 1")
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    
    def get_grid_config_by_id(self, config_id: int) -> Optional[Dict[str, Any]]:
        """根据ID获取网格配置"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM grid_configs WHERE id = ?", (config_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    
    def get_grid_trades(self, grid_config_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """获取网格轮次交易（按利润排序的成交记录）"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    ROW_NUMBER() OVER (ORDER BY created_at DESC) as round_num,
                    profit,
                    created_at,
                    side,
                    price,
                    quantity,
                    amount
                FROM trades 
                WHERE grid_config_id = ? AND profit != 0
                ORDER BY created_at DESC
                LIMIT ?
            """, (grid_config_id, limit))
            rows = cursor.fetchall()
            result = []
            for row in rows:
                d = dict(row)
                d['round_num'] = f"#{d['round_num']}"
                result.append(d)
            return result
        finally:
            conn.close()

    
    # ========== 挂单记录 ==========
    
    def save_order(self, grid_config_id: int, exchange_order_id: str,
                   symbol: str, side: str, price: float, quantity: float,
                   amount: float, grid_level: int = 0) -> int:
        """保存挂单记录"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO orders 
                (grid_config_id, exchange_order_id, symbol, side, 
                 price, quantity, amount, grid_level)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (grid_config_id, exchange_order_id, symbol, side,
                  price, quantity, amount, grid_level))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()
    
    def update_order_status(self, order_id: int, status: str):
        """更新挂单状态"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, (status, order_id))
            conn.commit()
        finally:
            conn.close()
    
    def get_open_orders(self, grid_config_id: int) -> List[Dict[str, Any]]:
        """获取指定策略的挂单"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM orders 
                WHERE grid_config_id = ? AND status = 'open'
                ORDER BY price DESC
            """, (grid_config_id,))
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()
    
    def get_buy_orders(self, grid_config_id: int) -> List[Dict[str, Any]]:
        """获取买单列表"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM orders 
                WHERE grid_config_id = ? AND side = 'buy' AND status = 'open'
                ORDER BY price DESC
            """, (grid_config_id,))
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()
    
    def get_sell_orders(self, grid_config_id: int) -> List[Dict[str, Any]]:
        """获取卖单列表"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM orders 
                WHERE grid_config_id = ? AND side = 'sell' AND status = 'open'
                ORDER BY price ASC
            """, (grid_config_id,))
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()
    
    # ========== 成交记录 ==========
    
    def save_trade(self, grid_config_id: int, exchange_order_id: str,
                   symbol: str, side: str, price: float, quantity: float,
                   amount: float, fee: float = 0, profit: float = 0) -> int:
        """保存成交记录"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO trades 
                (grid_config_id, exchange_order_id, symbol, side,
                 price, quantity, amount, fee, profit)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (grid_config_id, exchange_order_id, symbol, side,
                  price, quantity, amount, fee, profit))
            conn.commit()
            return cursor.lastrowid
        finally:
            conn.close()
    
    def get_trades(self, grid_config_id: int, limit: int = 100) -> List[Dict[str, Any]]:
        """获取成交记录"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM trades 
                WHERE grid_config_id = ?
                ORDER BY created_at DESC
                LIMIT ?
            """, (grid_config_id, limit))
            return [dict(row) for row in cursor.fetchall()]
        finally:
            conn.close()
    
    def get_trade_stats(self, grid_config_id: int) -> Dict[str, Any]:
        """获取交易统计"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT 
                    COUNT(*) as total_trades,
                    COALESCE(SUM(profit), 0) as total_profit,
                    COALESCE(SUM(fee), 0) as total_fees,
                    COUNT(CASE WHEN profit > 0 THEN 1 END) as win_trades,
                    COUNT(CASE WHEN profit < 0 THEN 1 END) as loss_trades
                FROM trades WHERE grid_config_id = ?
            """, (grid_config_id,))
            row = cursor.fetchone()
            stats = dict(row) if row else {}
            
            # 计算胜率
            total = stats.get('total_trades', 0)
            wins = stats.get('win_trades', 0)
            stats['win_rate'] = (wins / total * 100) if total > 0 else 0
            
            return stats
        finally:
            conn.close()
    
    # ========== 余额快照 ==========
    
    def save_balance_snapshot(self, grid_config_id: int, usdt_balance: float,
                              coin_balance: float, coin_price: float):
        """保存余额快照"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            total_value = usdt_balance + coin_balance * coin_price
            cursor.execute("""
                INSERT INTO balance_snapshots 
                (grid_config_id, usdt_balance, coin_balance, coin_price, total_value)
                VALUES (?, ?, ?, ?, ?)
            """, (grid_config_id, usdt_balance, coin_balance, coin_price, total_value))
            conn.commit()
        finally:
            conn.close()
    
    def get_latest_balance(self, grid_config_id: int) -> Optional[Dict[str, Any]]:
        """获取最新余额快照"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM balance_snapshots 
                WHERE grid_config_id = ?
                ORDER BY created_at DESC LIMIT 1
            """, (grid_config_id,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
    
    # ========== 用户管理 ==========
    
    def create_user(self, username: str, password_hash: str) -> bool:
        """创建用户"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO users (username, password_hash) VALUES (?, ?)",
                (username, password_hash)
            )
            conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False
        finally:
            conn.close()
    
    def get_user(self, username: str) -> Optional[Dict[str, Any]]:
        """获取用户信息"""
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM users WHERE username = ?", (username,))
            row = cursor.fetchone()
            return dict(row) if row else None
        finally:
            conn.close()
