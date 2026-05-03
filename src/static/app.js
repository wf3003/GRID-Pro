// ========== 全局状态 ==========
let ws = null;
let isRunning = false;
let currentPrice = 0;
let currentBalance = { usdt: 0, coin: 0 };
let tickerData = null;
let reconnectTimer = null;
let statusTimer = null;

// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', () => {
    loadApiKeys();
    loadSymbols();
    connectWebSocket();
    
    // 监听参数变化
    document.getElementById('exchange').addEventListener('change', () => {
        loadSymbols();
        updateCalculations();
    });
    document.getElementById('symbol').addEventListener('change', () => {
        updateTicker();
        updateCalculations();
    });
    document.getElementById('priceSpacing').addEventListener('input', () => {
        document.getElementById('priceSpacing').dataset.userModified = 'true';
        updateCalculations();
    });
    document.getElementById('gridCount').addEventListener('input', updateCalculations);
    document.getElementById('usdtAmount').addEventListener('input', updateCalculations);
    document.getElementById('coinAmount').addEventListener('input', updateCalculations);
    document.getElementById('feeRate').addEventListener('input', updateCalculations);
    document.getElementById('lowerPrice').addEventListener('input', updateCalculations);
    document.getElementById('upperPrice').addEventListener('input', updateCalculations);
    
    // 定时刷新价格
    setInterval(updateTicker, 10000);
    setInterval(updateBalance, 30000);
    
    // 定时刷新状态
    statusTimer = setInterval(updateStatus, 5000);
});

// ========== WebSocket ==========
function connectWebSocket() {
    const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
    const wsUrl = `${protocol}//${window.location.host}/ws`;
    
    try {
        ws = new WebSocket(wsUrl);
        
        ws.onopen = () => {
            document.getElementById('connectionStatus').textContent = '已连接';
            document.getElementById('connectionStatus').className = 'status-badge connected';
            addLog('WebSocket 已连接');
        };
        
        ws.onclose = () => {
            document.getElementById('connectionStatus').textContent = '已断开';
            document.getElementById('connectionStatus').className = 'status-badge disconnected';
            if (reconnectTimer) clearTimeout(reconnectTimer);
            reconnectTimer = setTimeout(connectWebSocket, 3000);
        };
        
        ws.onmessage = (event) => {
            try {
                const msg = JSON.parse(event.data);
                handleWsMessage(msg);
            } catch (e) {
                console.error('WS 消息解析失败:', e);
            }
        };
        
        ws.onerror = (e) => {
            console.error('WebSocket 错误:', e);
        };
        
        setInterval(() => {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send('ping');
            }
        }, 30000);
    } catch (e) {
        console.error('WebSocket 连接失败:', e);
        if (reconnectTimer) clearTimeout(reconnectTimer);
        reconnectTimer = setTimeout(connectWebSocket, 5000);
    }
}

function handleWsMessage(msg) {
    switch (msg.type) {
        case 'order_placed':
            addLog(`📌 挂单: ${msg.data.side.toUpperCase()} ${msg.data.price} x ${msg.data.quantity}`);
            break;
        case 'trade':
            addLog(`💹 成交: ${msg.data.side.toUpperCase()} ${msg.data.price} x ${msg.data.quantity} | 利润: $${msg.data.profit.toFixed(4)}`);
            updateStatus();
            break;
        case 'grid_adjusted':
            addLog(`🔄 网格调整: [${msg.data.old_range[0]}, ${msg.data.old_range[1]}] -> [${msg.data.new_range[0]}, ${msg.data.new_range[1]}]`);
            break;
        case 'status_update':
            updateUI(msg.data);
            break;
        case 'stopped':
            isRunning = false;
            document.getElementById('startBtn').disabled = false;
            document.getElementById('stopBtn').disabled = true;
            document.getElementById('runningPanel').style.display = 'none';
            document.getElementById('botConfigPanel').style.display = 'none';
            addLog('⏹ 网格策略已停止');
            break;
    }
}

// ========== API 调用 ==========
async function apiGet(url) {
    try {
        const res = await fetch(url);
        return await res.json();
    } catch (e) {
        console.error(`API GET ${url} 失败:`, e);
        return null;
    }
}

async function apiPost(url, data) {
    try {
        const res = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        return await res.json();
    } catch (e) {
        console.error(`API POST ${url} 失败:`, e);
        return { success: false, message: `请求失败: ${e.message}` };
    }
}

// ========== 加载数据 ==========
async function loadApiKeys() {
    const data = await apiGet('/api/keys');
    if (data && data.keys) {
        if (data.keys.binance) {
            document.getElementById('binanceApiKey').value = data.keys.binance.api_key || '';
            document.getElementById('binanceKeyStatus').textContent = data.keys.binance.configured ? '✅ 已配置' : '❌ 未配置';
        }
        if (data.keys.gateio) {
            document.getElementById('gateioApiKey').value = data.keys.gateio.api_key || '';
            document.getElementById('gateioKeyStatus').textContent = data.keys.gateio.configured ? '✅ 已配置' : '❌ 未配置';
        }
    }
}

async function loadSymbols() {
    const exchange = document.getElementById('exchange').value;
    const data = await apiGet(`/api/symbols?exchange=${exchange}`);
    const select = document.getElementById('symbol');
    
    if (data && data.symbols && data.symbols.length > 0) {
        select.innerHTML = '<option value="">-- 选择币种 --</option>';
        data.symbols.forEach(s => {
            const opt = document.createElement('option');
            opt.value = s.symbol;
            opt.textContent = s.symbol;
            select.appendChild(opt);
        });
        addLog(`已加载 ${data.symbols.length} 个交易对`);
    } else {
        select.innerHTML = '<option value="">加载失败</option>';
        addLog(`❌ 从 ${exchange} 加载币种列表失败`);
    }
}

async function updateTicker() {
    const exchange = document.getElementById('exchange').value;
    const symbol = document.getElementById('symbol').value;
    if (!symbol) return;
    
    const data = await apiGet(`/api/ticker?exchange=${exchange}&symbol=${symbol}`);
    if (data && data.price > 0) {
        currentPrice = data.price;
        tickerData = data;
        document.getElementById('symbolPrice').textContent = `$${data.price.toFixed(4)}`;
        document.getElementById('currentPrice').textContent = `$${data.price.toFixed(4)}`;
        
        if (data.high_24h && data.low_24h && data.high_24h > 0 && data.low_24h > 0) {
            const volatility = ((data.high_24h - data.low_24h) / data.low_24h) * 100;
            document.getElementById('volatility').textContent = `${volatility.toFixed(2)}%`;
            document.getElementById('optimalRange').textContent = 
                `$${data.low_24h.toFixed(4)} ~ $${data.high_24h.toFixed(4)}`;
            
            const optimalSpacing = (volatility / 26).toFixed(2);
            const spacingInput = document.getElementById('priceSpacing');
            if (!spacingInput.dataset.userModified) {
                spacingInput.value = Math.max(0.1, Math.min(5, optimalSpacing));
            }
        }
        
        updateCalculations();
    }
}

async function updateBalance() {
    const exchange = document.getElementById('exchange').value;
    const symbol = document.getElementById('symbol').value;
    if (!symbol) return;
    
    const baseAsset = symbol.split('/')[0];
    
    const usdtData = await apiGet(`/api/balance?exchange=${exchange}&asset=USDT`);
    if (usdtData) {
        currentBalance.usdt = usdtData.free || 0;
        document.getElementById('usdtBalance').textContent = `$${currentBalance.usdt.toFixed(2)}`;
        document.getElementById('availableUsdt').textContent = `$${currentBalance.usdt.toFixed(2)}`;
    }
    
    const coinData = await apiGet(`/api/balance?exchange=${exchange}&asset=${baseAsset}`);
    if (coinData) {
        currentBalance.coin = coinData.free || 0;
        document.getElementById('coinBalance').textContent = `${currentBalance.coin.toFixed(4)} ${baseAsset}`;
        document.getElementById('symbolBalance').textContent = `${currentBalance.coin.toFixed(4)}`;
        document.getElementById('availableCoin').textContent = `${currentBalance.coin.toFixed(4)} ${baseAsset}`;
    }
    
    updateCalculations();
}

// ========== 计算逻辑 ==========
function updateCalculations() {
    const price = currentPrice;
    if (!price || price <= 0) return;
    
    const spacing = parseFloat(document.getElementById('priceSpacing').value) || 0.5;
    const gridCount = parseInt(document.getElementById('gridCount').value) || 26;
    const usdtAmount = parseFloat(document.getElementById('usdtAmount').value) || 0;
    const coinAmount = parseFloat(document.getElementById('coinAmount').value) || 0;
    const feeRate = parseFloat(document.getElementById('feeRate').value) || 0.2;
    const lowerPrice = parseFloat(document.getElementById('lowerPrice').value) || 0;
    const upperPrice = parseFloat(document.getElementById('upperPrice').value) || 0;
    
    // 网格分配：一半买单，一半卖单
    const halfGrids = Math.floor(gridCount / 2);
    const buyGrids = Math.max(1, halfGrids - 1);  // 买单格数（扣除中间格）
    const sellGrids = Math.max(1, halfGrids - 1); // 卖单格数
    
    // 计算每格金额
    let perGridUsdt = 0;
    let perGridQty = 0;
    
    if (usdtAmount > 0 && coinAmount > 0) {
        // 两者都填：分别计算，取较大值
        const usdtPerGrid = usdtAmount / buyGrids;
        const coinPerGrid = coinAmount / sellGrids;
        perGridUsdt = Math.max(usdtPerGrid, coinPerGrid * price);
        perGridQty = perGridUsdt / price;
    } else if (usdtAmount > 0) {
        // 只填了 USDT
        perGridUsdt = usdtAmount / buyGrids;
        perGridQty = perGridUsdt / price;
    } else if (coinAmount > 0) {
        // 只填了币数量
        perGridQty = coinAmount / sellGrids;
        perGridUsdt = perGridQty * price;
    }
    
    // 计算所需最小金额
    const minUsdt = perGridUsdt * buyGrids;
    const minCoin = perGridQty * sellGrids;
    
    // 显示每格金额
    document.getElementById('perGridAmount').textContent = 
        `$${perGridUsdt.toFixed(2)} / ${perGridQty.toFixed(6)} 币`;
    
    // USDT 充足性检查
    const usdtHint = document.getElementById('usdtMinHint');
    if (usdtAmount > 0) {
        if (usdtAmount >= minUsdt) {
            usdtHint.textContent = `✅ 足够 (最少 $${minUsdt.toFixed(2)})`;
            usdtHint.className = 'hint-text valid';
        } else {
            usdtHint.textContent = `❌ 不足 (最少 $${minUsdt.toFixed(2)})`;
            usdtHint.className = 'hint-text invalid';
        }
    } else {
        usdtHint.textContent = '';
    }
    
    // 币充足性检查
    const coinHint = document.getElementById('coinMinHint');
    if (coinAmount > 0) {
        if (coinAmount >= minCoin) {
            coinHint.textContent = `✅ 足够 (最少 ${minCoin.toFixed(6)})`;
            coinHint.className = 'hint-text valid';
        } else {
            coinHint.textContent = `❌ 不足 (最少 ${minCoin.toFixed(6)})`;
            coinHint.className = 'hint-text invalid';
        }
    } else {
        coinHint.textContent = '';
    }
    
    // 需要额外购买的币
    let needBuy = 0;
    let needBuyUsdt = 0;
    if (coinAmount > 0 && currentBalance.coin < minCoin) {
        needBuy = minCoin - currentBalance.coin;
        needBuyUsdt = needBuy * price;
    } else if (usdtAmount > 0 && coinAmount === 0) {
        // 只填 USDT 时，需要买入所有币
        needBuy = minCoin - currentBalance.coin;
        needBuyUsdt = needBuy * price;
    }
    
    document.getElementById('needBuyCoin').textContent = 
        needBuy > 0.000001 
            ? `需要 ${needBuy.toFixed(6)} 币 (≈ $${needBuyUsdt.toFixed(2)})` 
            : '✅ 已足够';
    
    // 单格收益率
    const spacingPct = spacing / 100;
    const profitPct = spacingPct - (feeRate / 100) * 2;
    const profitPerTrade = profitPct * perGridUsdt;
    document.getElementById('estimatedProfit').value = 
        profitPct > 0 ? `${(profitPct * 100).toFixed(3)}% (≈ $${profitPerTrade.toFixed(4)})` : '亏损';
    
    // 网格间距（美元）
    const gridSpacing = price * spacingPct;
    document.getElementById('gridSpacing').textContent = `$${gridSpacing.toFixed(4)}`;
    document.getElementById('priceSpacingHint').textContent = `≈ $${gridSpacing.toFixed(4)}`;
    
    // 启动按钮状态
    const startBtn = document.getElementById('startBtn');
    if (usdtAmount > 0 && usdtAmount < minUsdt) {
        startBtn.disabled = true;
        startBtn.title = 'USDT 数量不足';
    } else if (coinAmount > 0 && coinAmount < minCoin) {
        startBtn.disabled = true;
        startBtn.title = '币数量不足';
    } else if (usdtAmount <= 0 && coinAmount <= 0) {
        startBtn.disabled = true;
        startBtn.title = '请填写 USDT 金额或币数量';
    } else {
        startBtn.disabled = isRunning;
        startBtn.title = '';
    }
}

// ========== 操作 ==========
async function saveApiKey(exchange) {
    const keyInput = document.getElementById(`${exchange}ApiKey`);
    const secretInput = document.getElementById(`${exchange}ApiSecret`);
    const statusSpan = document.getElementById(`${exchange}KeyStatus`);
    
    if (!keyInput.value || !secretInput.value) {
        statusSpan.textContent = '❌ 请填写完整';
        return;
    }
    
    const result = await apiPost('/api/keys', {
        exchange: exchange,
        api_key: keyInput.value,
        api_secret: secretInput.value
    });
    
    statusSpan.textContent = result.success ? '✅ 已保存' : `❌ ${result.message}`;
    if (result.success) {
        addLog(`✅ ${exchange} API Key 已保存`);
    }
}

async function testConnection(exchange) {
    const statusSpan = document.getElementById(`${exchange}KeyStatus`);
    statusSpan.textContent = '⏳ 测试中...';
    
    const result = await apiGet(`/api/ping?exchange=${exchange}`);
    statusSpan.textContent = result.connected ? '✅ 连接成功' : `❌ ${result.message}`;
    
    if (result.connected) {
        addLog(`✅ ${exchange} 连接成功, USDT 余额: $${result.usdt_balance}`);
        updateBalance();
    }
}

async function startGrid() {
    const exchange = document.getElementById('exchange').value;
    const symbol = document.getElementById('symbol').value;
    const priceSpacing = parseFloat(document.getElementById('priceSpacing').value) || 0.5;
    const gridCount = parseInt(document.getElementById('gridCount').value) || 26;
    const usdtAmount = parseFloat(document.getElementById('usdtAmount').value) || 0;
    const coinAmount = parseFloat(document.getElementById('coinAmount').value) || 0;
    const stopLossPrice = parseFloat(document.getElementById('stopLossPrice').value) || null;
    const feeRate = parseFloat(document.getElementById('feeRate').value) || 0.2;
    const lowerPrice = parseFloat(document.getElementById('lowerPrice').value) || null;
    const upperPrice = parseFloat(document.getElementById('upperPrice').value) || null;
    
    if (!symbol) {
        addLog('❌ 请选择币种');
        return;
    }
    
    if (usdtAmount <= 0) {
        addLog('❌ 请输入 USDT 数量');
        return;
    }
    
    addLog(`🚀 启动网格: ${symbol} @ ${exchange}, 间隔 ${priceSpacing}%, ${gridCount} 格`);
    
    const result = await apiPost('/api/start', {
        exchange: exchange,
        symbol: symbol,
        grid_count: gridCount,
        price_spacing: priceSpacing,
        usdt_amount: usdtAmount,
        coin_amount: coinAmount > 0 ? coinAmount : null,
        stop_loss_price: stopLossPrice,
        fee_rate: feeRate / 100,
        total_investment: usdtAmount,
        lower_price: lowerPrice,
        upper_price: upperPrice
    });
    
    if (result.success) {
        isRunning = true;
        document.getElementById('startBtn').disabled = true;
        document.getElementById('stopBtn').disabled = false;
        document.getElementById('runningPanel').style.display = 'block';
        document.getElementById('botConfigPanel').style.display = 'block';
        
        // 更新运行面板
        document.getElementById('runningSymbol').textContent = `${symbol} 现货网格`;
        document.getElementById('runningExchange').textContent = `${symbol} @ ${exchange}`;
        
        addLog(`✅ ${result.message}`);
        updateStatus();
    } else {
        addLog(`❌ ${result.detail || result.message || '启动失败'}`);
    }
}

async function stopGrid() {
    addLog('⏹ 正在停止网格...');
    
    const result = await apiPost('/api/stop', {});
    
    if (result.success) {
        isRunning = false;
        document.getElementById('startBtn').disabled = false;
        document.getElementById('stopBtn').disabled = true;
        document.getElementById('runningPanel').style.display = 'none';
        document.getElementById('botConfigPanel').style.display = 'none';
        addLog(`✅ ${result.message}`);
    } else {
        addLog(`❌ ${result.detail || result.message || '停止失败'}`);
    }
}

async function updateStatus() {
    const data = await apiGet('/api/status');
    if (data) {
        updateUI(data);
        if (data.is_running) {
            updateRunningPanel(data);
            updateBotConfigPanel(data);
        }
    }
}

function updateUI(data) {
    if (!data) return;
    
    if (data.strategy) {
        const s = data.strategy;
        document.getElementById('totalTrades').textContent = s.total_trades || 0;
        document.getElementById('totalProfit').textContent = `$${(s.total_pnl || 0).toFixed(4)}`;
        
        if (s.current_price) {
            document.getElementById('currentPrice').textContent = `$${s.current_price.toFixed(4)}`;
        }
    }
    
    if (data.buy_orders) {
        updateOrdersTable('buyOrdersBody', data.buy_orders, 'buy');
    }
    if (data.sell_orders) {
        updateOrdersTable('sellOrdersBody', data.sell_orders, 'sell');
    }
    
    if (data.trades) {
        updateTradesTable(data.trades);
    }
}

// ========== 运行详情面板 ==========
function updateRunningPanel(data) {
    if (!data || !data.is_running) {
        document.getElementById('runningPanel').style.display = 'none';
        return;
    }
    
    document.getElementById('runningPanel').style.display = 'block';
    
    // 基本信息
    const symbol = data.strategy?.symbol || '--';
    document.getElementById('runningSymbol').textContent = `${symbol} 现货网格`;
    document.getElementById('runningExchange').textContent = `${symbol} @ ${data.strategy?.exchange || '--'}`;
    document.getElementById('runningCreatedAt').textContent = data.created_at || '--';
    document.getElementById('runningTime').textContent = data.running_time || '--';
    
    // 价格区间
    const gridRange = data.strategy?.grid_range;
    if (gridRange && gridRange[0] > 0) {
        document.getElementById('runningPriceRange').textContent = 
            `${gridRange[0].toFixed(4)} - ${gridRange[1].toFixed(4)} USDT`;
    } else {
        document.getElementById('runningPriceRange').textContent = '--';
    }
    
    // 统计数据
    document.getElementById('runningTotalProfit').textContent = 
        data.total_profit >= 0 ? `+${data.total_profit}` : data.total_profit;
    document.getElementById('runningTotalProfit').className = 
        `running-stat-value ${data.total_profit >= 0 ? 'profit' : 'loss'}`;
    
    document.getElementById('runningGridProfit').textContent = 
        data.grid_profit >= 0 ? `+${data.grid_profit}` : data.grid_profit;
    document.getElementById('runningGridProfit').className = 
        `running-stat-value ${data.grid_profit >= 0 ? 'profit' : 'loss'}`;
    
    const floatingPnl = data.floating_pnl || 0;
    document.getElementById('runningFloatingPnl').textContent = 
        floatingPnl >= 0 ? `+${floatingPnl}` : floatingPnl;
    document.getElementById('runningFloatingPnl').className = 
        `running-stat-value ${floatingPnl >= 0 ? 'profit' : 'loss'}`;
    
    document.getElementById('runningAnnualized').textContent = 
        data.annualized_return ? `${data.annualized_return.toFixed(2)}%` : '0%';
    document.getElementById('runningAnnualized').className = 
        `running-stat-value ${data.annualized_return > 0 ? 'profit' : ''}`;
    
    document.getElementById('runningInvestment').textContent = 
        data.config?.total_investment ? `$${data.config.total_investment.toFixed(2)}` : '--';
    document.getElementById('runningTradeCount').textContent = 
        data.trade_stats?.total_trades || 0;
    
    // 网格收益轮次
    updateGridTradesTable(data.grid_trades);
    
    // 交易历史
    if (data.trades) {
        updateTradesTable(data.trades);
        updateHistoryTradesTable(data.trades);
    }
}

function updateGridTradesTable(gridTrades) {
    const tbody = document.getElementById('gridTradesBody');
    if (!gridTrades || gridTrades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="empty">暂无交易记录</td></tr>';
        return;
    }
    
    tbody.innerHTML = gridTrades.map(t => {
        const profit = t.profit || 0;
        const profitClass = profit > 0 ? 'profit' : (profit < 0 ? 'loss' : '');
        const profitText = profit > 0 ? `+${profit.toFixed(8)}` : (profit < 0 ? profit.toFixed(8) : '等待卖出');
        const dateStr = t.created_at ? formatDate(t.created_at) : '--';
        const sideText = t.side === 'buy' ? '买入' : (t.side === 'sell' ? '卖出' : '--');
        
        return `<tr>
            <td>${t.round_num || '--'}</td>
            <td class="${profitClass}">${profitText}</td>
            <td>${dateStr}</td>
            <td><span class="side-${t.side}">${sideText}</span></td>
        </tr>`;
    }).join('');
}

function updateHistoryTradesTable(trades) {
    const tbody = document.getElementById('historyTradesBody');
    if (!trades || trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty">暂无成交记录</td></tr>';
        return;
    }
    
    tbody.innerHTML = trades.map(t => `
        <tr>
            <td>${t.created_at || '--'}</td>
            <td><span class="side-${t.side}">${t.side === 'buy' ? '买入' : '卖出'}</span></td>
            <td>$${t.price.toFixed(4)}</td>
            <td>${t.quantity.toFixed(6)}</td>
            <td>$${(t.price * t.quantity).toFixed(2)}</td>
            <td>$${(t.fee || 0).toFixed(4)}</td>
            <td class="${t.profit > 0 ? 'profit' : t.profit < 0 ? 'loss' : ''}">${t.profit ? `$${t.profit.toFixed(4)}` : '--'}</td>
        </tr>
    `).join('');
}

// ========== 机器人参数面板 ==========
function updateBotConfigPanel(data) {
    if (!data || !data.config) return;
    
    const config = data.config;
    const strategy = data.strategy || {};
    
    document.getElementById('botPriceRange').textContent = 
        config.lower_price && config.upper_price 
            ? `${config.lower_price} - ${config.upper_price} USDT`
            : (strategy.grid_range ? `${strategy.grid_range[0].toFixed(4)} - ${strategy.grid_range[1].toFixed(4)}` : '--');
    
    document.getElementById('botGridCount').textContent = 
        `${config.grid_count || '--'} / 等差`;
    
    document.getElementById('botPerGrid').textContent = 
        config.total_investment && config.grid_count 
            ? (config.total_investment / config.grid_count).toFixed(2)
            : '--';
    
    document.getElementById('botProfitRate').textContent = 
        config.price_spacing 
            ? `${(config.price_spacing * 0.8).toFixed(2)}%-${(config.price_spacing * 1.2).toFixed(2)}%`
            : '--';
    
    document.getElementById('botId').textContent = config.id || '--';
    document.getElementById('botStartCondition').textContent = '触发价格 0 USDT';
    document.getElementById('botStopCondition').textContent = '触发价格 0 USDT';
    document.getElementById('botStopPrices').textContent = 
        `${config.stop_loss_price || 0} / ${config.stop_loss_price || 0}`;
    document.getElementById('botQtyIncrease').textContent = `按数量 --`;
    document.getElementById('botMoveRange').textContent = '1%';
    document.getElementById('botStopMovePrices').textContent = '-- / --';
    document.getElementById('botSellOnStop').textContent = '否';
    document.getElementById('botShareRatio').textContent = '0%';
    document.getElementById('botName').textContent = `${config.symbol || '--'} 现货网格`;
}

// ========== 操作按钮 ==========
function shareBot() {
    const symbol = document.getElementById('runningSymbol').textContent;
    const text = `🤖 ${symbol}\n总收益: ${document.getElementById('runningTotalProfit').textContent} USDT\n网格收益: ${document.getElementById('runningGridProfit').textContent} USDT`;
    
    if (navigator.clipboard) {
        navigator.clipboard.writeText(text).then(() => {
            addLog('📤 已复制分享文本到剪贴板');
        });
    } else {
        addLog('📤 分享功能需要 HTTPS');
    }
}

async function terminateBot() {
    if (confirm('确定要终止机器人吗？')) {
        await stopGrid();
    }
}

function copyBot() {
    const symbol = document.getElementById('runningSymbol').textContent;
    const text = `🤖 ${symbol}\n配置已复制`;
    
    if (navigator.clipboard) {
        navigator.clipboard.writeText(text).then(() => {
            addLog('📋 已复制机器人信息到剪贴板');
        });
    }
}

// ========== 面板切换 ==========
function toggleGridTrades() {
    const panel = document.getElementById('gridTradesPanel');
    const icon = document.getElementById('gridTradesToggle');
    if (panel.style.display === 'none') {
        panel.style.display = 'block';
        icon.textContent = '▼';
    } else {
        panel.style.display = 'none';
        icon.textContent = '▶';
    }
}

function toggleTradeHistory() {
    const panel = document.getElementById('tradeHistoryPanel');
    const icon = document.getElementById('tradeHistoryToggle');
    if (panel.style.display === 'none') {
        panel.style.display = 'block';
        icon.textContent = '▼';
    } else {
        panel.style.display = 'none';
        icon.textContent = '▶';
    }
}

function toggleBotConfig() {
    const body = document.getElementById('botConfigBody');
    const icon = document.getElementById('botConfigToggle');
    if (body.style.display === 'none') {
        body.style.display = 'block';
        icon.textContent = '▼';
    } else {
        body.style.display = 'none';
        icon.textContent = '▶';
    }
}

// ========== 表格更新 ==========
function updateOrdersTable(bodyId, orders, side) {
    const tbody = document.getElementById(bodyId);
    if (!orders || orders.length === 0) {
        tbody.innerHTML = '<tr><td colspan="4" class="empty">暂无订单</td></tr>';
        return;
    }
    
    tbody.innerHTML = orders.map(o => `
        <tr>
            <td>$${o.price.toFixed(4)}</td>
            <td>${o.quantity.toFixed(6)}</td>
            <td>$${(o.price * o.quantity).toFixed(2)}</td>
            <td><span class="status-${o.status || 'open'}">${o.status || '挂单中'}</span></td>
        </tr>
    `).join('');
}

function updateTradesTable(trades) {
    const tbody = document.getElementById('tradesBody');
    if (!trades || trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="7" class="empty">暂无成交记录</td></tr>';
        return;
    }
    
    tbody.innerHTML = trades.map(t => `
        <tr>
            <td>${t.created_at || '--'}</td>
            <td><span class="side-${t.side}">${t.side === 'buy' ? '买入' : '卖出'}</span></td>
            <td>$${t.price.toFixed(4)}</td>
            <td>${t.quantity.toFixed(6)}</td>
            <td>$${(t.price * t.quantity).toFixed(2)}</td>
            <td>$${(t.fee || 0).toFixed(4)}</td>
            <td class="${t.profit > 0 ? 'profit' : t.profit < 0 ? 'loss' : ''}">${t.profit ? `$${t.profit.toFixed(4)}` : '--'}</td>
        </tr>
    `).join('');
}

// ========== 辅助函数 ==========
function formatDate(dateStr) {
    try {
        const d = new Date(dateStr);
        const month = String(d.getMonth() + 1).padStart(2, '0');
        const day = String(d.getDate()).padStart(2, '0');
        const hours = String(d.getHours()).padStart(2, '0');
        const minutes = String(d.getMinutes()).padStart(2, '0');
        return `${month}-${day} ${hours}:${minutes}`;
    } catch {
        return dateStr;
    }
}

// ========== 日志 ==========
function addLog(message) {
    const container = document.getElementById('logContainer');
    const time = new Date().toLocaleTimeString();
    const div = document.createElement('div');
    div.className = 'log-entry';
    div.textContent = `[${time}] ${message}`;
    container.appendChild(div);
    container.scrollTop = container.scrollHeight;
    
    while (container.children.length > 200) {
        container.removeChild(container.firstChild);
    }
}

// ========== UI 辅助 ==========
function toggleApiPanel() {
    const panel = document.getElementById('apiPanel');
    const icon = document.getElementById('apiToggleIcon');
    if (panel.style.display === 'none') {
        panel.style.display = 'block';
        icon.textContent = '▼';
    } else {
        panel.style.display = 'none';
        icon.textContent = '▶';
    }
}
