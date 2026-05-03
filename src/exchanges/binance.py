"""Binance 交易所实现"""
import hashlib
import hmac
import time
from decimal import Decimal
from typing import Optional, List, Tuple
from urllib.parse import urlencode

import aiohttp

from src.exchanges.base import ExchangeBase
from src.models.trading import Order, OrderSide, OrderStatus, OrderType, Ticker, Balance


class BinanceExchange(ExchangeBase):
    """Binance 交易所"""
    
    BASE_URL = "https://api.binance.com"
    
    def __init__(self, api_key: str, api_secret: str, base_url: str = ""):
        super().__init__(
            name="binance",
            api_key=api_key,
            api_secret=api_secret,
            base_url=base_url or self.BASE_URL
        )
        self._session: Optional[aiohttp.ClientSession] = None
    
    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session
    
    def _sign_request(self, params: dict) -> dict:
        """签名请求参数"""
        query_string = urlencode(params)
        signature = hmac.new(
            self.api_secret.encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        params['signature'] = signature
        return params
    
    def _get_headers(self) -> dict:
        return {
            'X-MBX-APIKEY': self.api_key,
            'Content-Type': 'application/x-www-form-urlencoded'
        }
    
    async def _request(self, method: str, path: str, signed: bool = False, params: dict = None) -> dict:
        """发送请求"""
        session = await self._get_session()
        url = f"{self.base_url}{path}"
        
        headers = self._get_headers()
        if signed:
            params = params or {}
            params['timestamp'] = int(time.time() * 1000)
            params = self._sign_request(params)
        
        async with session.request(method, url, params=params, headers=headers) as response:
            data = await response.json()
            if response.status != 200:
                raise Exception(f"Binance API error: {data}")
            return data
    
    async def get_ticker(self, symbol: str) -> Ticker:
        """获取行情"""
        data = await self._request("GET", "/api/v3/ticker/bookTicker", params={"symbol": symbol})
        ticker_data = await self._request("GET", "/api/v3/ticker/24hr", params={"symbol": symbol})
        
        return Ticker(
            exchange=self.name,
            symbol=symbol,
            bid=Decimal(data['bidPrice']),
            ask=Decimal(data['askPrice']),
            last=Decimal(ticker_data['lastPrice']),
            volume=Decimal(ticker_data['volume']),
            high_24h=Decimal(ticker_data.get('highPrice', '0')),
            low_24h=Decimal(ticker_data.get('lowPrice', '0'))
        )
    
    async def get_balance(self, asset: str) -> Balance:
        """获取余额"""
        data = await self._request("GET", "/api/v3/account", signed=True)
        
        for balance in data['balances']:
            if balance['asset'] == asset:
                free = Decimal(balance['free'])
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
        params = {
            'symbol': symbol,
            'side': side.value.upper(),
            'type': 'LIMIT',
            'timeInForce': 'GTC',
            'price': str(price),
            'quantity': str(quantity)
        }
        
        data = await self._request("POST", "/api/v3/order", signed=True, params=params)
        
        return Order(
            id=data['orderId'],
            exchange=self.name,
            symbol=symbol,
            side=OrderSide(data['side'].lower()),
            price=Decimal(data['price']),
            quantity=Decimal(data['origQty']),
            filled_quantity=Decimal(data['executedQty']),
            status=OrderStatus(data['status'].lower()),
            type=OrderType.LIMIT
        )
    
    async def cancel_order(self, symbol: str, order_id: str) -> bool:
        """取消订单"""
        try:
            params = {
                'symbol': symbol,
                'orderId': order_id
            }
            await self._request("DELETE", "/api/v3/order", signed=True, params=params)
            return True
        except Exception:
            return False
    
    async def get_order(self, symbol: str, order_id: str) -> Optional[Order]:
        """获取订单状态"""
        try:
            params = {
                'symbol': symbol,
                'orderId': order_id
            }
            data = await self._request("GET", "/api/v3/order", signed=True, params=params)
            
            return Order(
                id=data['orderId'],
                exchange=self.name,
                symbol=symbol,
                side=OrderSide(data['side'].lower()),
                price=Decimal(data['price']),
                quantity=Decimal(data['origQty']),
                filled_quantity=Decimal(data['executedQty']),
                status=OrderStatus(data['status'].lower()),
                type=OrderType.LIMIT
            )
        except Exception:
            return None
    
    async def get_open_orders(self, symbol: str) -> List[Order]:
        """获取当前挂单"""
        params = {'symbol': symbol}
        data = await self._request("GET", "/api/v3/openOrders", signed=True, params=params)
        
        orders = []
        for item in data:
            orders.append(Order(
                id=item['orderId'],
                exchange=self.name,
                symbol=symbol,
                side=OrderSide(item['side'].lower()),
                price=Decimal(item['price']),
                quantity=Decimal(item['origQty']),
                filled_quantity=Decimal(item['executedQty']),
                status=OrderStatus(item['status'].lower()),
                type=OrderType.LIMIT
            ))
        return orders
    
    async def get_trading_fee(self, symbol: str) -> Decimal:
        """获取交易费率"""
        params = {'symbol': symbol}
        data = await self._request("GET", "/api/v3/account/commission", signed=True, params=params)
        # Binance 返回的是千分比，如 0.001 表示 0.1%
        return Decimal(str(data['takerCommission']))
    
    async def get_symbol_precision(self, symbol: str) -> Tuple[int, int]:
        """获取交易对精度"""
        data = await self._request("GET", "/api/v3/exchangeInfo")
        
        for s in data['symbols']:
            if s['symbol'] == symbol:
                price_precision = s['quotePrecision']
                qty_precision = s['baseAssetPrecision']
                return price_precision, qty_precision
        
        raise ValueError(f"Symbol {symbol} not found")
    
    async def get_trading_pairs(self, quote_asset: str = "USDT") -> List[dict]:
        """获取可交易币种列表"""
        data = await self._request("GET", "/api/v3/exchangeInfo")
        
        pairs = []
        for s in data['symbols']:
            if s['quoteAsset'] != quote_asset:
                continue
            if s['status'] != 'TRADING':
                continue
            
            pairs.append({
                'symbol': f"{s['baseAsset']}/{s['quoteAsset']}",
                'base_asset': s['baseAsset'],
                'quote_asset': s['quoteAsset'],
                'price_precision': s['quotePrecision'],
                'qty_precision': s['baseAssetPrecision'],
                'min_qty': float(s['filters'][1]['minQty']) if len(s['filters']) > 1 else 0,
                'min_amount': float(s['filters'][2]['minNotional']) if len(s['filters']) > 2 else 0,
                'status': s['status']
            })
        
        return pairs
    
    async def close(self):
        """关闭会话"""
        if self._session and not self._session.closed:
            await self._session.close()
