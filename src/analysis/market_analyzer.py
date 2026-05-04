"""市场技术分析模块

提供多周期 K 线技术分析，包括：
- 均线系统 (MA5, MA10, MA20, MA60)
- 趋势判断
- 支撑位 / 阻力位
- RSI 指标
- 波动率分析
- 资金流量估算
"""
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from statistics import mean, stdev

from loguru import logger

from src.exchanges.base import ExchangeBase
from src.models.trading import Kline


# 分析周期配置
ANALYSIS_INTERVALS = ["15m", "30m", "1h", "4h", "1d", "1w"]
INTERVAL_LABELS = {
    "15m": "15分钟",
    "30m": "30分钟",
    "1h": "1小时",
    "4h": "4小时",
    "1d": "日线",
    "1w": "周线"
}

# 每个周期获取的 K 线数量
INTERVAL_LIMITS = {
    "15m": 96,   # 24小时
    "30m": 96,   # 48小时
    "1h": 168,   # 7天
    "4h": 126,   # 21天
    "1d": 90,    # 90天
    "1w": 52     # 52周
}


def calculate_sma(prices: List[float], period: int) -> Optional[float]:
    """计算简单移动平均线"""
    if len(prices) < period:
        return None
    return sum(prices[-period:]) / period


def calculate_ema(prices: List[float], period: int) -> Optional[float]:
    """计算指数移动平均线"""
    if len(prices) < period:
        return None
    
    # 使用 SMA 作为初始值
    sma = sum(prices[:period]) / period
    multiplier = 2 / (period + 1)
    
    ema = sma
    for price in prices[period:]:
        ema = (price - ema) * multiplier + ema
    
    return ema


def calculate_rsi(prices: List[float], period: int = 14) -> Optional[float]:
    """计算 RSI 指标"""
    if len(prices) < period + 1:
        return None
    
    gains = []
    losses = []
    
    for i in range(len(prices) - period, len(prices)):
        diff = prices[i] - prices[i - 1]
        if diff > 0:
            gains.append(diff)
            losses.append(0)
        else:
            gains.append(0)
            losses.append(abs(diff))
    
    avg_gain = sum(gains) / period
    avg_loss = sum(losses) / period
    
    if avg_loss == 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    rsi = 100 - (100 / (1 + rs))
    return rsi


def find_support_resistance(highs: List[float], lows: List[float], current_price: float = 0) -> Tuple[List[float], List[float]]:
    """寻找支撑位和阻力位（基于局部极值）"""
    supports = []
    resistances = []
    
    if len(highs) < 10:
        return supports, resistances
    
    # 寻找局部低点（支撑）
    for i in range(2, len(lows) - 2):
        if lows[i] < lows[i-1] and lows[i] < lows[i-2] and lows[i] < lows[i+1] and lows[i] < lows[i+2]:
            supports.append(lows[i])
    
    # 寻找局部高点（阻力）
    for i in range(2, len(highs) - 2):
        if highs[i] > highs[i-1] and highs[i] > highs[i-2] and highs[i] > highs[i+1] and highs[i] > highs[i+2]:
            resistances.append(highs[i])
    
    # 去重合并相近的值
    supports = _merge_nearby_values(supports, 0.005)  # 0.5% 范围内合并
    resistances = _merge_nearby_values(resistances, 0.005)
    
    # 按当前价格过滤：支撑 < 当前价格，阻力 > 当前价格
    if current_price > 0:
        supports = [s for s in supports if s < current_price]
        resistances = [r for r in resistances if r > current_price]
    
    # 取离当前价格最近的3个
    supports = sorted(supports, key=lambda x: abs(x - current_price))[:3] if current_price > 0 else sorted(supports)[-3:]
    resistances = sorted(resistances, key=lambda x: abs(x - current_price))[:3] if current_price > 0 else sorted(resistances)[-3:]
    
    # 按价格排序输出
    supports.sort()
    resistances.sort()
    
    return supports, resistances


def _merge_nearby_values(values: List[float], threshold_pct: float) -> List[float]:
    """合并相近的值"""
    if not values:
        return []
    
    sorted_vals = sorted(values)
    merged = [sorted_vals[0]]
    
    for v in sorted_vals[1:]:
        if abs(v - merged[-1]) / merged[-1] > threshold_pct:
            merged.append(v)
    
    return merged


def calculate_volatility(prices: List[float]) -> float:
    """计算波动率（标准差/均值）"""
    if len(prices) < 2:
        return 0.0
    
    avg = mean(prices)
    if avg == 0:
        return 0.0
    
    return stdev(prices) / avg


def estimate_money_flow(klines: List[Kline]) -> Dict:
    """估算资金流量
    
    基于成交量加权价格变化来估算资金流入/流出
    """
    if len(klines) < 2:
        return {
            "net_flow": 0,
            "inflow": 0,
            "outflow": 0,
            "flow_ratio": 0
        }
    
    inflow = 0.0
    outflow = 0.0
    
    for kline in klines:
        close = float(kline.close)
        open_ = float(kline.open)
        volume = float(kline.volume)
        quote_volume = float(kline.quote_volume)
        
        # 使用更精确的资金流估算
        # 如果有主动买卖数据直接用
        if kline.taker_buy_quote_volume > 0:
            inflow += float(kline.taker_buy_quote_volume)
            outflow += quote_volume - float(kline.taker_buy_quote_volume)
        else:
            # 没有主动买卖数据时，用价格变化估算
            # 收盘价 > 开盘价 => 资金流入
            # 收盘价 < 开盘价 => 资金流出
            if close > open_:
                inflow += quote_volume * (close - open_) / close
            elif close < open_:
                outflow += quote_volume * (open_ - close) / close
    
    net_flow = inflow - outflow
    total = inflow + outflow
    flow_ratio = inflow / total if total > 0 else 0.5
    
    return {
        "net_flow": round(net_flow, 2),
        "inflow": round(inflow, 2),
        "outflow": round(outflow, 2),
        "flow_ratio": round(flow_ratio, 4)
    }


def analyze_trend(ma_short: Optional[float], ma_mid: Optional[float], ma_long: Optional[float]) -> str:
    """判断趋势方向"""
    if ma_short is None or ma_mid is None:
        return "数据不足"
    
    if ma_short > ma_mid:
        if ma_long is not None and ma_mid > ma_long:
            return "📈 多头排列 (强势上涨)"
        return "📈 短期上涨"
    elif ma_short < ma_mid:
        if ma_long is not None and ma_mid < ma_long:
            return "📉 空头排列 (强势下跌)"
        return "📉 短期下跌"
    else:
        return "➡️ 横盘震荡"


def get_rsi_signal(rsi: Optional[float]) -> str:
    """RSI 信号判断"""
    if rsi is None:
        return "数据不足"
    if rsi >= 70:
        return "⚠️ 超买 (可能回调)"
    elif rsi >= 60:
        return "📈 偏强"
    elif rsi >= 40:
        return "➡️ 中性"
    elif rsi >= 30:
        return "📉 偏弱"
    else:
        return "⚠️ 超卖 (可能反弹)"


class MarketAnalyzer:
    """市场技术分析器"""
    
    def __init__(self, exchange: ExchangeBase):
        self.exchange = exchange
    
    async def analyze(self, symbol: str) -> Dict:
        """对指定币种进行多周期技术分析
        
        Returns:
            dict: 包含所有周期分析结果的字典
        """
        result = {
            "symbol": symbol,
            "exchange": self.exchange.name,
            "current_price": 0,
            "intervals": {}
        }
        
        # 获取当前价格
        try:
            ticker = await self.exchange.get_ticker(symbol)
            current_price = float(ticker.last)
            result["current_price"] = current_price
        except Exception as e:
            logger.error(f"获取 {symbol} 当前价格失败: {e}")
            current_price = 0
        
        # 分析每个周期
        for interval in ANALYSIS_INTERVALS:
            try:
                limit = INTERVAL_LIMITS.get(interval, 100)
                klines = await self.exchange.get_klines(symbol, interval, limit)
                
                analysis = self._analyze_interval(klines, interval, current_price)
                result["intervals"][interval] = analysis
                
            except Exception as e:
                logger.error(f"分析 {symbol} {interval} 周期失败: {e}")
                result["intervals"][interval] = {
                    "error": str(e),
                    "label": INTERVAL_LABELS.get(interval, interval)
                }
        
        return result
    
    def get_trading_advice(self, analysis_result: Dict) -> Dict:
        """根据多周期分析结果生成综合交易建议
        
        Args:
            analysis_result: analyze() 方法的返回结果
            
        Returns:
            dict: 包含综合建议的字典
        """
        intervals = analysis_result.get("intervals", {})
        current_price = analysis_result.get("current_price", 0)
        
        if not intervals or current_price == 0:
            return {
                "score": 0,
                "level": "数据不足",
                "summary": "无法获取足够数据进行分析",
                "detail": "",
                "suggestions": [],
                "risk_warnings": []
            }
        
        # 评分系统 (0-100)
        score = 50  # 默认中性
        reasons = []
        warnings = []
        suggestions = []
        
        # 1. 多周期趋势一致性检查
        trend_scores = {"📈": 1, "➡️": 0, "📉": -1}
        trend_values = []
        for interval_name, data in intervals.items():
            if "trend" in data and data["trend"] != "数据不足":
                for key, val in trend_scores.items():
                    if data["trend"].startswith(key):
                        trend_values.append(val)
                        break
        
        if trend_values:
            avg_trend = sum(trend_values) / len(trend_values)
            if avg_trend > 0.3:
                score += 15
                reasons.append("多周期趋势偏多")
            elif avg_trend < -0.3:
                score -= 15
                reasons.append("多周期趋势偏空")
            else:
                reasons.append("多周期趋势分歧/震荡")
        
        # 2. RSI 综合判断
        rsi_values = []
        for interval_name, data in intervals.items():
            if data.get("rsi") is not None:
                rsi_values.append(data["rsi"])
        
        if rsi_values:
            avg_rsi = sum(rsi_values) / len(rsi_values)
            min_rsi = min(rsi_values)
            max_rsi = max(rsi_values)
            
            if avg_rsi > 65:
                score -= 10
                warnings.append(f"多周期RSI偏高({avg_rsi:.0f})，短期可能回调")
            elif avg_rsi < 35:
                score += 10
                reasons.append(f"多周期RSI偏低({avg_rsi:.0f})，可能超跌反弹")
            
            if max_rsi >= 70:
                warnings.append(f"有周期RSI≥70超买，注意回调风险")
            if min_rsi <= 30:
                reasons.append(f"有周期RSI≤30超卖，可能反弹")
        
        # 3. 价格位置判断（相对近期高低点）
        all_highs = []
        all_lows = []
        for interval_name, data in intervals.items():
            if data.get("high"):
                all_highs.append(data["high"])
            if data.get("low"):
                all_lows.append(data["low"])
        
        if all_highs and all_lows and current_price > 0:
            recent_high = max(all_highs)
            recent_low = min(all_lows)
            range_pct = (recent_high - recent_low) / recent_low * 100 if recent_low > 0 else 0
            
            if range_pct > 0:
                pos_pct = (current_price - recent_low) / (recent_high - recent_low) * 100
                
                if pos_pct > 80:
                    score -= 10
                    warnings.append(f"价格处于近期高位({pos_pct:.0f}%分位)，追高风险大")
                elif pos_pct < 20:
                    score += 10
                    reasons.append(f"价格处于近期低位({pos_pct:.0f}%分位)，布局机会")
                else:
                    reasons.append(f"价格处于适中位置({pos_pct:.0f}%分位)")
        
        # 4. 波动率判断
        volatilities = []
        for interval_name, data in intervals.items():
            if data.get("volatility") is not None:
                volatilities.append(data["volatility"])
        
        if volatilities:
            avg_vol = sum(volatilities) / len(volatilities)
            if avg_vol > 3:
                warnings.append(f"波动率偏高({avg_vol:.1f}%)，网格收益空间大但风险也大")
                score += 5  # 网格喜欢波动
            elif avg_vol < 0.5:
                reasons.append(f"波动率偏低({avg_vol:.1f}%)，网格收益空间有限")
                score -= 5
        
        # 5. 成交量判断
        volume_ratios = []
        for interval_name, data in intervals.items():
            if data.get("volume_ratio") is not None:
                volume_ratios.append(data["volume_ratio"])
        
        if volume_ratios:
            avg_vr = sum(volume_ratios) / len(volume_ratios)
            if avg_vr > 1.5:
                reasons.append("成交量放大，市场活跃")
                score += 5
            elif avg_vr < 0.5:
                warnings.append("成交量萎缩，市场冷清")
                score -= 5
        
        # 6. 资金流量判断
        net_flows = []
        for interval_name, data in intervals.items():
            mf = data.get("money_flow", {})
            if mf.get("net_flow") is not None:
                net_flows.append(mf["net_flow"])
        
        if net_flows:
            avg_flow = sum(net_flows) / len(net_flows)
            if avg_flow > 0:
                reasons.append("资金净流入")
                score += 5
            elif avg_flow < 0:
                warnings.append("资金净流出")
                score -= 5
        
        # 7. 网格适合度评分
        grid_suitability = "适合" if score >= 50 else "谨慎"
        
        # 生成建议
        if score >= 70:
            summary = "✅ 非常适合开网格"
            detail = "多周期技术指标整体向好，价格位置适中，适合启动网格策略"
            suggestions = [
                "✅ 可以启动网格交易",
                "📊 建议设置中等网格数量(20-30格)",
                "💰 可适当增加投资金额",
                "📉 建议设置止损价格"
            ]
        elif score >= 50:
            summary = "⚠️ 可以开网格，注意控制仓位"
            detail = "技术指标中性偏正面，可以启动但建议控制仓位"
            suggestions = [
                "⚠️ 可以启动网格交易",
                "📊 建议设置适中网格数量(20-26格)",
                "💰 建议分批建仓，不要一次性投入全部资金",
                "📉 务必设置止损价格"
            ]
        elif score >= 30:
            summary = "⚠️ 谨慎开网格，建议等待"
            detail = "技术指标偏弱，建议等待更好的入场时机"
            suggestions = [
                "⏸ 建议暂时观望",
                "📊 如果一定要开，减少网格数量(15-20格)",
                "💰 控制仓位，只用部分资金",
                "📉 止损价格设紧一些"
            ]
        else:
            summary = "❌ 不建议开网格"
            detail = "技术指标明显偏空，此时开网格容易被套"
            suggestions = [
                "❌ 强烈建议等待",
                "📉 当前下跌趋势明显",
                "💡 等价格企稳、指标好转后再考虑",
                "📊 可以关注支撑位是否有反弹信号"
            ]
        
        # 风险警告
        if warnings:
            risk_warnings = warnings[:3]  # 最多3条
        else:
            risk_warnings = ["无明显风险信号"]
        
        return {
            "score": max(0, min(100, score)),
            "level": "适合" if score >= 50 else "谨慎",
            "summary": summary,
            "detail": detail,
            "suggestions": suggestions,
            "risk_warnings": risk_warnings,
            "reasons": reasons[:5],  # 最多5条
            "grid_suitability": grid_suitability,
            "current_price": current_price,
            "recent_high": max(all_highs) if all_highs else 0,
            "recent_low": min(all_lows) if all_lows else 0
        }
    
    def _analyze_interval(
        self,
        klines: List[Kline],
        interval: str,
        current_price: float
    ) -> Dict:
        """分析单个周期的数据"""
        if not klines:
            return {"label": INTERVAL_LABELS.get(interval, interval), "error": "无数据"}
        
        # 提取价格数据
        closes = [float(k.close) for k in klines]
        highs = [float(k.high) for k in klines]
        lows = [float(k.low) for k in klines]
        opens = [float(k.open) for k in klines]
        
        # 计算均线
        ma5 = calculate_sma(closes, 5)
        ma10 = calculate_sma(closes, 10)
        ma20 = calculate_sma(closes, 20)
        ma60 = calculate_sma(closes, 60)
        
        # 计算 EMA
        ema12 = calculate_ema(closes, 12)
        ema26 = calculate_ema(closes, 26)
        
        # 计算 RSI
        rsi = calculate_rsi(closes, 14)
        
        # 趋势判断
        trend = analyze_trend(ma5, ma20, ma60)
        
        # 支撑位和阻力位
        supports, resistances = find_support_resistance(highs, lows, current_price)
        
        # 波动率
        volatility = calculate_volatility(closes)
        
        # 涨跌幅
        if len(closes) >= 2:
            change_pct = ((closes[-1] - closes[0]) / closes[0]) * 100
        else:
            change_pct = 0
        
        # 最新一根 K 线涨跌
        if len(closes) >= 2:
            last_change = ((closes[-1] - closes[-2]) / closes[-2]) * 100
        else:
            last_change = 0
        
        # 资金流量估算
        money_flow = estimate_money_flow(klines)
        
        # 成交量分析
        volumes = [float(k.volume) for k in klines]
        avg_volume = mean(volumes) if volumes else 0
        last_volume = volumes[-1] if volumes else 0
        volume_ratio = last_volume / avg_volume if avg_volume > 0 else 0
        
        # 最高价 / 最低价
        period_high = max(highs) if highs else 0
        period_low = min(lows) if lows else 0
        
        return {
            "label": INTERVAL_LABELS.get(interval, interval),
            "interval": interval,
            "current_price": current_price,
            "open": opens[-1] if opens else 0,
            "high": period_high,
            "low": period_low,
            "close": closes[-1] if closes else 0,
            "change_pct": round(change_pct, 2),
            "last_change_pct": round(last_change, 2),
            "ma5": round(ma5, 4) if ma5 else None,
            "ma10": round(ma10, 4) if ma10 else None,
            "ma20": round(ma20, 4) if ma20 else None,
            "ma60": round(ma60, 4) if ma60 else None,
            "ema12": round(ema12, 4) if ema12 else None,
            "ema26": round(ema26, 4) if ema26 else None,
            "rsi": round(rsi, 2) if rsi else None,
            "rsi_signal": get_rsi_signal(rsi),
            "trend": trend,
            "supports": [round(s, 4) for s in supports],
            "resistances": [round(r, 4) for r in resistances],
            "volatility": round(volatility * 100, 2),  # 转为百分比
            "volume": round(last_volume, 2),
            "avg_volume": round(avg_volume, 2),
            "volume_ratio": round(volume_ratio, 2),
            "money_flow": money_flow,
            "data_points": len(klines)
        }
