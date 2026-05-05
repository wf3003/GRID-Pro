"""Gate.io 交易所实现"""
import asyncio
import hashlib
import hmac
import time
from datetime import datetime
from decimal import Decimal
from typing import Optional, List, Tuple
from urllib.parse import urlencode

import aiohttp
from loguru import logger

from src.exchanges.base import ExchangeBase
from src.models.trading import Order, OrderSide, OrderStatus, OrderType, Ticker, Balance, Kline


class GateIOExchange(ExchangeBase):
    """Gate.io 交易所"""
    
    BASE_URL = "https://api.gateio.ws"
    
    def __init__(self, api_key: str, api_secret: str, base_url: str = ""):
        super().__init__(
            name="gateio",
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url or self.BASE_URL
        )
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession(
                connector=aiohttp.TCPConnector(force_close=True),
                trust_env=False
            )
        return self._session
    
    def _sign_request(self, method: str, path: str, query_params: str = "", body: str = "") -> dict:
        """Gate.io V4 API 签名
        参考: https://www.gate.com/docs/developers/apiv4/en/#api-signature-string-generation
        签名串格式: METHOD + \n + URL + \n + QUERY_STRING + \n + HexEncode(SHA512(body)) + \n + TIMESTAMP
        """
        t = str(int(time.time()))
        
        # 对 body 做 SHA512 哈希并转十六进制
        m = hashlib.sha512()
        m.update(body.encode('utf-8'))
        hashed_payload = m.hexdigest()
        
        # 签名串: method + \n + path + \n + query_string + \n + hashed_payload + \n + timestamp
        sign_string = f"{method}\n{path}\n{query_params}\n{hashed_payload}\n{t}"
        
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            sign_string.encode('utf-8'),
            hashlib.sha512
        ).hexdigest()
        
        return {
            'KEY': self.api_key,
            'Timestamp': t,
            'SIGN': signature,
            'Content-Type': 'application/json'
        }
    
    async def _request(self, method: str, path: str, signed: bool = False, params: dict = None) -> dict:
        """发送请求"""
        session = await self._get_session()
        url = f"{self.base_url}{path}"
        
        headers = {}
        query_string = ""
        body = ""
        
        if signed:
            if method in ["GET", "DELETE"] and params:
                query_string = urlencode(params)
                url = f"{url}?{query_string}"
            elif method == "POST" and params:
                import json
                body = json.dumps(params)
                headers['Content-Type'] = 'application/json'
            
            sign_headers = self._sign_request(method, path, query_string, body)
            headers.update(sign_headers)
        elif params:
            query_string = urlencode(params)
            url = f"{url}?{query_string}"
        
        async with session.request(method, url, headers=headers, data=body if body else None) as response:
            data = await response.json()
            if response.status not in [200, 201]:
                raise Exception(f"Gate.io API error: {data}")
            return data
    
    async def get_ticker(self, symbol: str) -> Ticker:
        """获取行情"""
        # Gate.io 使用下划线格式，如 BTC_USDT
        gate_symbol = symbol.replace("/", "_")
        data = await self._request("GET", f"/api/v4/spot/tickers", params={"currency_pair": gate_symbol})
        
        ticker_data = data[0] if isinstance(data, list) else data
        
        return Ticker(
            exchange=self.name,
            symbol=symbol,
            bid=Decimal(ticker_data['highest_bid']),
            ask=Decimal(ticker_data['lowest_ask']),
            last=Decimal(ticker_data['last']),
            volume=Decimal(ticker_data['base_volume']),
            high_24h=Decimal(ticker_data.get('high_24h', '0')),
            low_24h=Decimal(ticker_data.get('low_24h', '0'))
        )
    
    async def get_balance(self, asset: str) -> Balance:
        """获取余额"""
        data = await self._request("GET", "/api/v4/spot/accounts", signed=True)
        
        for balance in data:
            if balance['currency'] == asset:
                free = Decimal(balance['available'])
                locked = Decimal(balance['locked'])
                return Balance(
                    exchange=self.name,
                    asset=asset,
                    free=free,
                    locked=locked,
                    total=free + locked
                )
        return Balance(
            exchange=self.name,
            asset=asset,
            free=Decimal("0"),
            locked=Decimal("0"),
            total=Decimal("0")
        )
    
    async def create_limit_order(
        self,
        symbol: str,
        side: OrderSide,
        price: Decimal,
        quantity: Decimal
    ) -> Order:
        """创建限价单"""
        gate_symbol = symbol.replace("/", "_")
        
        params = {
            'currency_pair': gate_symbol,
            'side': side.value.lower(),
            'type': 'limit',
            'price': str(price),
            'amount': str(quantity),
            'time_in_force': 'gtc'
        }
        
        data = await self._request("POST", "/api/v4/spot/orders", signed=True, params=params)
        
        return Order(
            id=data['id'],
            exchange=self.name,
            symbol=symbol,
            side=OrderSide(data['side']),
            price=Decimal(data['price']),
            quantity=Decimal(data['amount']),
            filled_quantity=Decimal(data.get('filled_amount', '0')),
            status=self._parse_status(data['status']),
            type=OrderType.LIMIT
        )
    
    def _parse_status(self, status: str) -> OrderStatus:
        """解析 Gate.io 订单状态"""
        status_map = {
            'open': OrderStatus.OPEN,
            'filled': OrderStatus.FILLED,
            'cancelled': OrderStatus.CANCELLED,
            'expired': OrderStatus.CANCELLED,
            'failed': OrderStatus.FAILED,
        }
        return status_map.get(status, OrderStatus.PENDING)
    
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """取消订单"""
        try:
            gate_symbol = symbol.replace("/", "_")
            # Gate.io 取消订单 API: DELETE /api/v4/spot/orders/{order_id}?currency_pair={pair}
            params = {
                'currency_pair': gate_symbol,
            }
            await self._request("DELETE", f"/api/v4/spot/orders/{order_id}", signed=True, params=params)
            return True
        except Exception as e:
            logger.error(f"取消订单失败: {e}")
            return False
    
    async def get_order(self, symbol: str, order_id: str) -> Optional[Order]:
        """获取订单状态"""
        try:
            gate_symbol = symbol.replace("/", "_")
            params = {'currency_pair': gate_symbol}
            data = await self._request("GET", f"/api/v4/spot/orders/{order_id}", signed=True, params=params)
            
            return Order(
                id=data['id'],
                exchange=self.name,
                symbol=symbol,
                side=OrderSide(data['side']),
                price=Decimal(data['price']),
                quantity=Decimal(data['amount']),
                filled_quantity=Decimal(data.get('filled_amount', '0')),
                status=self._parse_status(data['status']),
                type=OrderType.LIMIT
            )
        except Exception:
            return None
    
    async def get_open_orders(self, symbol: str) -> List[Order]:
        """获取当前挂单"""
        gate_symbol = symbol.replace("/", "_")
        params = {'currency_pair': gate_symbol}
        data = await self._request("GET", "/api/v4/spot/open_orders", signed=True, params=params)
        
        orders = []
        for item in data:
            orders.append(Order(
                id=item['id'],
                exchange=self.name,
                symbol=symbol,
                side=OrderSide(item['side']),
                price=Decimal(item['price']),
                quantity=Decimal(item['amount']),
                filled_quantity=Decimal(item.get('filled_amount', '0')),
                status=self._parse_status(item['status']),
                type=OrderType.LIMIT
            ))
        return orders
    
    async def get_trading_fee(self, symbol: str) -> Decimal:
        """获取交易费率"""
        data = await self._request("GET", "/api/v4/spot/fee", signed=True)
        # Gate.io 返回的是百分比，如 0.002 表示 0.2%
        return Decimal(str(data['taker_fee']))
    
    async def get_symbol_precision(self, symbol: str) -> Tuple[int, int]:
        """获取交易对精度"""
        gate_symbol = symbol.replace("/", "_")
        data = await self._request("GET", "/api/v4/spot/currency_pairs")
        
        for pair in data:
            if pair['id'] == gate_symbol:
                # Gate.io 返回的 precision 是整数（如 5 表示 5 位小数）
                price_prec = int(pair['precision'])
                qty_prec = int(pair['amount_precision'])
                return price_prec, qty_prec
        
        raise ValueError(f"Symbol {symbol} not found")
    
    async def get_min_order_amount(self, symbol: str) -> Decimal:
        """获取最小订单金额（计价货币，如 USDT）"""
        gate_symbol = symbol.replace("/", "_")
        data = await self._request("GET", "/api/v4/spot/currency_pairs")
        
        for pair in data:
            if pair['id'] == gate_symbol:
                return Decimal(str(pair.get('min_quote_amount', '3')))
        
        return Decimal("3")  # Gate.io 默认最小 3 USDT
    
    async def get_trading_pairs(self, quote_asset: str = "USDT") -> List[dict]:
        """获取可交易币种列表（按币种名称排序）"""
        # 用独立 session，避免 session 复用问题
        async with aiohttp.ClientSession() as session:
            pairs_url = f"{self.base_url}/api/v4/spot/currency_pairs"
            try:
                async with session.get(pairs_url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    pairs_data = await resp.json()
            except Exception:
                return []
        
        pairs = []
        for pair in pairs_data:
            if pair.get('quote') != quote_asset:
                continue
            if pair.get('trade_status') != 'tradable':
                continue
            
            pairs.append({
                'symbol': f"{pair['base']}/{pair['quote']}",
                'base_asset': pair['base'],
                'quote_asset': pair['quote'],
                'price_precision': int(pair['precision']),
                'qty_precision': int(pair['amount_precision']),
                'min_qty': float(pair['min_base_amount']),
                'min_amount': float(pair['min_quote_amount']),
                'status': 'TRADING',
                'quote_volume': 0
            })
        
        # 按币种名称字母排序
        pairs.sort(key=lambda x: x['base_asset'])
        
        return pairs
    
    async def get_klines(
        self,
        symbol: str,
        interval: str = "1h",
        limit: int = 100
    ) -> List[Kline]:
        """获取 K 线数据"""
        gate_symbol = symbol.replace("/", "_")
        
        # Gate.io 周期映射
        interval_map = {
            '1m': '1m', '5m': '5m', '15m': '15m', '30m': '30m',
            '1h': '1h', '4h': '4h', '1d': '1d', '1w': '7d'
        }
        gate_interval = interval_map.get(interval, '1h')
        
        params = {
            'currency_pair': gate_symbol,
            'interval': gate_interval,
            'limit': limit
        }
        
        data = await self._request("GET", "/api/v4/spot/candlesticks", params=params)
        
        klines = []
        for item in data:
            # Gate.io K 线格式:
            # [timestamp, volume, close, high, low, open, amount]
            # amount = quote volume
            ts = int(item[0])
            klines.append(Kline(
                exchange=self.name,
                symbol=symbol,
                interval=interval,
                open_time=datetime.fromtimestamp(ts),
                close_time=datetime.fromtimestamp(ts + self._interval_seconds(interval)),
                open=Decimal(item[5]),
                high=Decimal(item[3]),
                low=Decimal(item[4]),
                close=Decimal(item[2]),
                volume=Decimal(item[1]),
                quote_volume=Decimal(item[6])
            ))
        
        return klines
    
    def _interval_seconds(self, interval: str) -> int:
        """将周期字符串转换为秒数"""
        units = {
            'm': 60, 'h': 3600, 'd': 86400, 'w': 604800
        }
        num = int(interval[:-1])
        unit = interval[-1]
        return num * units.get(unit, 3600)
    
    async def close(self):
        """关闭会话"""
        if self._session and not self._session.closed:
            await self._session.close()
