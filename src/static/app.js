// ========== 全局状态 ==========
let ws = null;
let isRunning = false;
let currentPrice = 0;
let currentBalance = { usdt: 0, coin: 0 };
let tickerData = null;
let reconnectTimer = null;
let statusTimer = null;
let adviceLoadedSymbol = '';   // 记录已加载建议的币种
let analysisLoadedSymbol = ''; // 记录已加载分析的币种

// ========== Token 管理 ==========
function getToken() {
    return localStorage.getItem('grid_pro_token');
}

function getUsername() {
    return localStorage.getItem('grid_pro_username') || '';
}

function logout() {
    localStorage.removeItem('grid_pro_token');
    localStorage.removeItem('grid_pro_username');
    window.location.href = '/';
}

// 检查登录状态
const token = getToken();
if (!token) {
    window.location.href = '/';
}

// 所有 fetch 请求自动带 Authorization header
const originalFetch = window.fetch;
window.fetch = function(url, options = {}) {
    const token = getToken();
    if (token) {
        options = options || {};
        options.headers = options.headers || {};
        if (Array.isArray(options.headers)) {
            options.headers.push(['Authorization', 'Bearer ' + token]);
        } else {
            options.headers['Authorization'] = 'Bearer ' + token;
        }
    }
    return originalFetch.call(this, url, options).then(async res => {
        if (res.status === 401) {
            if (window.location.pathname === '/app' || window.location.pathname === '/') {
                logout();
            }
        }
        return res;
    });
};

// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', () => {
    const username = getUsername();
    if (username) {
        document.getElementById('headerUsername').textContent = '👤 ' + username;
    }
    
    loadApiKeys();
    loadSymbols();
    connectWebSocket();
    
    document.getElementById('exchange').addEventListener('change', () => {
        loadSymbols();
        updateCalculations();
        adviceLoadedSymbol = '';
        analysisLoadedSymbol = '';
    });
    document.getElementById('symbol').addEventListener('change', () => {
        const symbol = document.getElementById('symbol').value;
        if (symbol) {
            updateTicker();
            updateBalance();
            updateCalculations();
            // 重置加载标记，让 loadAdvice/loadAnalysis 重新加载
            adviceLoadedSymbol = '';
            analysisLoadedSymbol = '';
            document.getElementById('advicePanel').style.display = 'block';
            document.getElementById('adviceToggleIcon').textContent = '▼';
            loadAdvice();
            document.getElementById('analysisPanel').style.display = 'block';
            document.getElementById('analysisToggleIcon').textContent = '▼';
            loadAnalysis();
        }
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
    
    setInterval(updateTicker, 10000);
    setInterval(updateBalance, 30000);
    statusTimer = setInterval(updateStatus, 5000);
    
    // 页面加载后立即检查状态，避免刷新后按钮状态错误
    setTimeout(checkInitialStatus, 500);
});

// ========== 页面初始化状态检查 ==========
async function checkInitialStatus() {
    const data = await apiGet('/api/status');
    if (data) {
        if (data.is_running) {
            // 网格正在运行
            isRunning = true;
            document.getElementById('startBtn').disabled = true;
            document.getElementById('stopBtn').disabled = false;
            document.getElementById('runningPanel').style.display = 'block';
            document.getElementById('botConfigPanel').style.display = 'block';
            updateUI(data);
            updateRunningPanel(data);
            updateBotConfigPanel(data);
            addLog('🔄 检测到运行中的网格策略');
        } else if (data.has_active_config) {
            // 数据库中有运行中的配置但内存中丢失（服务重启），正在尝试恢复
            addLog('🔄 检测到运行中的网格配置，正在尝试恢复...');
            // 5秒后再次检查
            setTimeout(checkInitialStatus, 5000);
        }
    }
}

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
        case 'recovered':
            isRunning = true;
            document.getElementById('startBtn').disabled = true;
            document.getElementById('stopBtn').disabled = false;
            document.getElementById('runningPanel').style.display = 'block';
            document.getElementById('botConfigPanel').style.display = 'block';
            addLog(`🔄 已自动恢复网格: ${msg.data.symbol} @ ${msg.data.exchange} (${msg.data.order_count} 个挂单)`);
            updateStatus();
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
        
        if (data.symbols.length > 0 && !select.value) {
            select.value = data.symbols[0].symbol;
            select.dispatchEvent(new Event('change'));
        }
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
        document.getElementById('symbolPrice').textContent = `$${formatPrice(data.price)}`;
        document.getElementById('currentPrice').textContent = `$${formatPrice(data.price)}`;
        
        if (data.high_24h && data.low_24h && data.high_24h > 0 && data.low_24h > 0) {
            const volatility = ((data.high_24h - data.low_24h) / data.low_24h) * 100;
            document.getElementById('volatility').textContent = `${volatility.toFixed(2)}%`;
            document.getElementById('optimalRange').textContent = 
                `$${formatPrice(data.low_24h)} ~ $${formatPrice(data.high_24h)}`;
            
            const feeRate = parseFloat(document.getElementById('feeRate').value) || 0.1;
            const minSpacing = feeRate * 2;
            const optimalSpacing = (volatility / 26).toFixed(2);
            const spacingInput = document.getElementById('priceSpacing');
            if (!spacingInput.dataset.userModified) {
                spacingInput.value = Math.max(minSpacing, Math.min(5, optimalSpacing));
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
        const availableUsdtEl = document.getElementById('availableUsdt');
        if (availableUsdtEl) {
            availableUsdtEl.textContent = `$${currentBalance.usdt.toFixed(2)}`;
        }
    }
    
    const coinData = await apiGet(`/api/balance?exchange=${exchange}&asset=${baseAsset}`);
    if (coinData) {
        currentBalance.coin = coinData.free || 0;
        const symbolBalanceEl = document.getElementById('symbolBalance');
        if (symbolBalanceEl) {
            symbolBalanceEl.textContent = `${currentBalance.coin.toFixed(4)}`;
        }
        const availableCoinEl = document.getElementById('availableCoin');
        if (availableCoinEl) {
            availableCoinEl.textContent = `${currentBalance.coin.toFixed(4)} ${baseAsset}`;
        }
    }
    
    updateCalculations();
}

// ========== 计算逻辑 ==========
function updateCalculations() {
    const price = currentPrice;
    if (!price || price <= 0) {
        document.getElementById('priceSpacingHint').textContent = '价格间隔: 等待价格数据...';
        document.getElementById('netProfitHint').textContent = '';
        document.getElementById('perGridAmount').textContent = '--';
        document.getElementById('needBuyCoin').textContent = '--';
        document.getElementById('usdtMinHint').textContent = '';
        document.getElementById('coinMinHint').textContent = '';
        document.getElementById('minOrderHint').textContent = '';
        return;
    }
    
    const spacing = parseFloat(document.getElementById('priceSpacing').value) || 0.5;
    const gridCount = parseInt(document.getElementById('gridCount').value) || 26;
    const usdtAmount = parseFloat(document.getElementById('usdtAmount').value) || 0;
    const coinAmount = parseFloat(document.getElementById('coinAmount').value) || 0;
    const feeRate = parseFloat(document.getElementById('feeRate').value) || 0.2;
    const lowerPrice = parseFloat(document.getElementById('lowerPrice').value) || 0;
    const upperPrice = parseFloat(document.getElementById('upperPrice').value) || 0;
    
    // 后端实际使用的格数：总格数/2 - 1（保留1格缓冲）
    const halfGrids = Math.floor(gridCount / 2);
    const buyGrids = Math.max(1, halfGrids - 1);  // 买单格数
    const sellGrids = Math.max(1, halfGrids - 1);  // 卖单格数
    const totalGrids = buyGrids + sellGrids;  // 总交易格数
    
    // 后端计算：per_grid_usdt = usdt_amount / total_grids（无预留比例）
    // 后端在 _place_initial_orders 中单独计算 need_coin/need_usdt 时加 1 格缓冲
    let perGridUsdt = 0;  // 每格 USDT 金额（买卖对称）
    let perGridQty = 0;   // 每格币数量（买卖对称）
    let totalUsdtNeeded = 0;  // 总共需要的 USDT
    let totalCoinNeeded = 0;  // 总共需要的币
    let needBuy = 0;      // 需要买入的币
    let needBuyUsdt = 0;  // 需要买入币花费的 USDT
    
    if (usdtAmount > 0 && coinAmount > 0) {
        // 用户同时填了 USDT 和币数量
        perGridUsdt = usdtAmount / totalGrids;
        perGridQty = coinAmount / totalGrids;
        totalUsdtNeeded = perGridUsdt * (buyGrids + 1);  // 买单 + 1格缓冲
        totalCoinNeeded = perGridQty * (sellGrids + 1);   // 卖单 + 1格缓冲
    } else if (usdtAmount > 0) {
        // 用户只填了 USDT 金额
        // 后端：per_grid_usdt = usdt_amount / total_grids
        // 后端：per_grid_qty = per_grid_usdt / price
        perGridUsdt = usdtAmount / totalGrids;
        perGridQty = perGridUsdt / price;
        totalUsdtNeeded = perGridUsdt * (buyGrids + 1);  // 买单 + 1格缓冲
        totalCoinNeeded = perGridQty * (sellGrids + 1);   // 卖单 + 1格缓冲
        // 如果账户币不够，需要买入
        if (currentBalance.coin < totalCoinNeeded) {
            needBuy = totalCoinNeeded - currentBalance.coin;
            needBuyUsdt = needBuy * price;
        }
    } else if (coinAmount > 0) {
        // 用户只填了币数量
        perGridQty = coinAmount / totalGrids;
        perGridUsdt = perGridQty * price;
        totalUsdtNeeded = perGridUsdt * (buyGrids + 1);
        totalCoinNeeded = perGridQty * (sellGrids + 1);
    }
    
    const orderUsdt = perGridUsdt * buyGrids;  // 买单总金额
    const orderCoin = perGridQty * sellGrids;   // 卖单总币数
    
    const MIN_ORDER_USDT = 1.0;
    const perGridUsdtBuy = perGridUsdt;
    const perGridUsdtSell = perGridQty * price;
    
    document.getElementById('perGridAmount').textContent = 
        `$${perGridUsdt.toFixed(2)} / ${formatQty(perGridQty)} 币`;
    
    const minOrderHint = document.getElementById('minOrderHint');
    if (minOrderHint) {
        if (perGridUsdtBuy < MIN_ORDER_USDT || perGridUsdtSell < MIN_ORDER_USDT) {
            minOrderHint.textContent = `❌ 每格金额 $${Math.min(perGridUsdtBuy, perGridUsdtSell).toFixed(2)} 低于最小 $${MIN_ORDER_USDT}，请增加资金或减少网格数`;
            minOrderHint.className = 'hint-text invalid';
        } else {
            minOrderHint.textContent = `✅ 每格 ≥ $${MIN_ORDER_USDT}，满足最小交易要求`;
            minOrderHint.className = 'hint-text valid';
        }
    }
    
    const usdtHint = document.getElementById('usdtMinHint');
    if (usdtAmount > 0) {
        if (usdtAmount >= totalUsdtNeeded) {
            usdtHint.textContent = `✅ 足够 (买单: $${orderUsdt.toFixed(2)}, 预留: $${(perGridUsdt).toFixed(2)}, 买币: $${needBuyUsdt.toFixed(2)})`;
            usdtHint.className = 'hint-text valid';
        } else if (usdtAmount >= orderUsdt) {
            usdtHint.textContent = `⚠️ 仅够挂单 (建议预留 1格: $${perGridUsdt.toFixed(2)})`;
            usdtHint.className = 'hint-text warning';
        } else {
            usdtHint.textContent = `❌ 不足 (最少 $${totalUsdtNeeded.toFixed(2)})`;
            usdtHint.className = 'hint-text invalid';
        }
    } else {
        usdtHint.textContent = '';
    }
    
    const coinHint = document.getElementById('coinMinHint');
    if (coinAmount > 0) {
        if (coinAmount >= totalCoinNeeded) {
            coinHint.textContent = `✅ 足够 (挂单: ${formatQty(orderCoin)}, 预留: ${formatQty(perGridQty)})`;
            coinHint.className = 'hint-text valid';
        } else if (coinAmount >= orderCoin) {
            coinHint.textContent = `⚠️ 仅够挂单 (建议预留 1格: ${formatQty(perGridQty)})`;
            coinHint.className = 'hint-text warning';
        } else {
            coinHint.textContent = `❌ 不足 (最少 ${formatQty(totalCoinNeeded)})`;
            coinHint.className = 'hint-text invalid';
        }
    } else {
        coinHint.textContent = '';
    }
    
    document.getElementById('needBuyCoin').textContent = 
        needBuy > 0.000001 
            ? `需要 ${formatQty(needBuy)} 币 (≈ $${needBuyUsdt.toFixed(2)})` 
            : '✅ 已足够';
    
    const spacingPct = spacing / 100;
    const totalFeePct = (feeRate / 100) * 2;
    const profitPct = spacingPct - totalFeePct;
    const profitPerTrade = profitPct * perGridUsdt;
    
    const netProfitHint = document.getElementById('netProfitHint');
    if (netProfitHint) {
        const netPct = profitPct * 100;
        if (netPct > 0) {
            netProfitHint.textContent = `净收益率: +${netPct.toFixed(3)}% (≈ $${profitPerTrade.toFixed(4)})`;
            netProfitHint.className = 'hint-text valid';
        } else if (netPct === 0) {
            netProfitHint.textContent = `净收益率: 0% (刚好够手续费)`;
            netProfitHint.className = 'hint-text';
        } else {
            netProfitHint.textContent = `净收益率: ${netPct.toFixed(3)}% ❌ 亏损 (手续费已 ${(totalFeePct * 100).toFixed(2)}%)`;
            netProfitHint.className = 'hint-text invalid';
        }
    }
    
    const gridSpacing = price * spacingPct;
    document.getElementById('priceSpacingHint').textContent = `价格间隔: $${formatPrice(gridSpacing)}`;
    
    const startBtn = document.getElementById('startBtn');
    if (usdtAmount > 0 && usdtAmount < orderUsdt) {
        startBtn.disabled = true;
        startBtn.title = 'USDT 数量不足';
    } else if (coinAmount > 0 && coinAmount < orderCoin) {
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
        // 保存成功后立即刷新余额
        updateBalance();
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
        
        if (s.current_price) {
            document.getElementById('currentPrice').textContent = `$${formatPrice(s.current_price)}`;
        }
        
        const totalProfit = data.total_profit || 0;
        const totalInvestment = data.config?.total_investment || 0;
        const totalProfitEl = document.getElementById('totalProfit');
        const profitPct = totalInvestment > 0 ? (totalProfit / totalInvestment) * 100 : 0;
        totalProfitEl.textContent = `${totalProfit.toFixed(2)} ${profitPct >= 0 ? '+' : ''}${profitPct.toFixed(2)}%`;
        totalProfitEl.className = `info-value ${totalProfit >= 0 ? 'profit' : 'loss'}`;
        
        const gridProfit = data.grid_profit || 0;
        const gridProfitEl = document.getElementById('gridProfit');
        gridProfitEl.textContent = `${gridProfit >= 0 ? '+' : ''}${gridProfit.toFixed(2)}`;
        gridProfitEl.className = `info-value ${gridProfit >= 0 ? 'profit' : 'loss'}`;
        
        const floatingPnl = data.floating_pnl || 0;
        const floatingPnlEl = document.getElementById('floatingPnl');
        floatingPnlEl.textContent = `${floatingPnl >= 0 ? '+' : ''}${floatingPnl.toFixed(2)}`;
        floatingPnlEl.className = `info-value ${floatingPnl >= 0 ? 'profit' : 'loss'}`;
        
        const annualized = data.annualized_return || 0;
        const annualizedEl = document.getElementById('annualizedReturn');
        annualizedEl.textContent = `${annualized >= 0 ? '+' : ''}${annualized.toFixed(2)}%`;
        annualizedEl.className = `info-value ${annualized >= 0 ? 'profit' : 'loss'}`;
        
        document.getElementById('totalInvestment').textContent = totalInvestment.toFixed(2);
        document.getElementById('totalTrades').textContent = s.total_trades || 0;
        document.getElementById('runningTimeDisplay').textContent = data.running_time || '--';
        
        const gridRange = s.grid_range;
        if (gridRange && gridRange[0] > 0) {
            document.getElementById('priceRangeDisplay').textContent = 
                `${formatPrice(gridRange[0])} - ${formatPrice(gridRange[1])}`;
        } else {
            document.getElementById('priceRangeDisplay').textContent = '--';
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
    
    const symbol = data.strategy?.symbol || '--';
    document.getElementById('runningSymbol').textContent = `${symbol} 现货网格`;
    document.getElementById('runningExchange').textContent = `${symbol} @ ${data.strategy?.exchange || '--'}`;
    
    // 修复时间显示：后端 created_at 是 UTC 时间，转北京时间
    if (data.created_at) {
        try {
            const utcDate = new Date(data.created_at.replace(' ', 'T') + 'Z');
            const beijingTime = utcDate.toLocaleString('zh-CN', {
                timeZone: 'Asia/Shanghai',
                year: 'numeric',
                month: '2-digit',
                day: '2-digit',
                hour: '2-digit',
                minute: '2-digit',
                second: '2-digit',
                hour12: false
            });
            document.getElementById('runningCreatedAt').textContent = beijingTime;
        } catch (e) {
            document.getElementById('runningCreatedAt').textContent = data.created_at;
        }
    } else {
        document.getElementById('runningCreatedAt').textContent = '--';
    }
    
    document.getElementById('runningTime').textContent = data.running_time || '--';
    
    const gridRange = data.strategy?.grid_range;
    if (gridRange && gridRange[0] > 0) {
        document.getElementById('runningPriceRange').textContent = 
            `${gridRange[0].toFixed(4)} - ${gridRange[1].toFixed(4)} USDT`;
    } else {
        document.getElementById('runningPriceRange').textContent = '--';
    }
    
    document.getElementById('runningTotalProfit').textContent = 
        data.total_profit >= 0 ? `+${data.total_profit.toFixed(2)}` : data.total_profit.toFixed(2);
    document.getElementById('runningTotalProfit').className = 
        `running-stat-value ${data.total_profit >= 0 ? 'profit' : 'loss'}`;
    
    document.getElementById('runningGridProfit').textContent = 
        data.grid_profit >= 0 ? `+${data.grid_profit.toFixed(2)}` : data.grid_profit.toFixed(2);
    document.getElementById('runningGridProfit').className = 
        `running-stat-value ${data.grid_profit >= 0 ? 'profit' : 'loss'}`;
    
    const floatingPnl = data.floating_pnl || 0;
    document.getElementById('runningFloatingPnl').textContent = 
        floatingPnl >= 0 ? `+${floatingPnl.toFixed(2)}` : floatingPnl.toFixed(2);
    document.getElementById('runningFloatingPnl').className = 
        `running-stat-value ${floatingPnl >= 0 ? 'profit' : 'loss'}`;
    
    document.getElementById('runningAnnualized').textContent = 
        data.annualized_return ? `${data.annualized_return.toFixed(2)}%` : '0%';
    document.getElementById('runningAnnualized').className = 
        `running-stat-value ${data.annualized_return > 0 ? 'profit' : ''}`;
    
    // 总投资 = USDT余额 + 币价值（折算成 USDT）
    let totalInvestmentValue = 0;
    if (data.balance) {
        const usdtBalance = data.balance.usdt_balance || 0;
        const coinBalance = data.balance.coin_balance || 0;
        const currentPrice = data.strategy?.current_price || 0;
        totalInvestmentValue = usdtBalance + (coinBalance * currentPrice);
    }
    document.getElementById('runningInvestment').textContent = 
        totalInvestmentValue > 0 ? `$${totalInvestmentValue.toFixed(2)}` : '--';
    document.getElementById('runningTradeCount').textContent = 
        data.trade_stats?.total_trades || 0;
    
    updateGridTradesTable(data.grid_trades);
    
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
}

// ========== 交易建议面板 ==========
async function loadAdvice() {
    const exchange = document.getElementById('exchange').value;
    const symbol = document.getElementById('symbol').value;
    if (!symbol) return;
    
    // 如果已经加载过该币种，不再重复加载
    if (adviceLoadedSymbol === symbol) return;
    adviceLoadedSymbol = symbol;
    
    const advicePanel = document.getElementById('advicePanel');
    const adviceContent = document.getElementById('adviceContent');
    
    adviceContent.innerHTML = '<div class="loading">⏳ 加载中...</div>';
    advicePanel.style.display = 'block';
    
    const data = await apiGet(`/api/advice?exchange=${exchange}&symbol=${symbol}`);
    
    if (!data || data.error) {
        adviceContent.innerHTML = `<div class="error">❌ 获取建议失败: ${data?.error || '未知错误'}</div>`;
        return;
    }
    
    const score = data.score || 0;
    const scoreColor = score >= 70 ? '#22c55e' : (score >= 50 ? '#eab308' : (score >= 30 ? '#f97316' : '#ef4444'));
    
    let suggestionsHtml = '';
    if (data.suggestions && data.suggestions.length > 0) {
        suggestionsHtml = data.suggestions.map(s => `<div class="suggestion-item">${s}</div>`).join('');
    }
    
    let warningsHtml = '';
    if (data.risk_warnings && data.risk_warnings.length > 0) {
        warningsHtml = data.risk_warnings.map(w => `<div class="warning-item">⚠️ ${w}</div>`).join('');
    }
    
    adviceContent.innerHTML = `
        <div class="advice-header">
            <div class="advice-score" style="color: ${scoreColor}">
                <span class="score-value">${score}</span>
                <span class="score-label">/ 100</span>
            </div>
            <div class="advice-summary">${data.summary || '--'}</div>
        </div>
        <div class="advice-detail">${data.detail || ''}</div>
        ${suggestionsHtml ? `<div class="advice-section"><div class="section-title">📋 建议</div>${suggestionsHtml}</div>` : ''}
        ${warningsHtml ? `<div class="advice-section"><div class="section-title">⚠️ 风险提示</div>${warningsHtml}</div>` : ''}
    `;
}

// ========== 技术分析面板 ==========
async function loadAnalysis() {
    const exchange = document.getElementById('exchange').value;
    const symbol = document.getElementById('symbol').value;
    if (!symbol) return;
    
    // 如果已经加载过该币种，不再重复加载
    if (analysisLoadedSymbol === symbol) return;
    analysisLoadedSymbol = symbol;
    
    const analysisPanel = document.getElementById('analysisPanel');
    const analysisContent = document.getElementById('analysisContent');
    
    analysisContent.innerHTML = '<div class="loading">⏳ 加载中...</div>';
    analysisPanel.style.display = 'block';
    
    const data = await apiGet(`/api/analysis?exchange=${exchange}&symbol=${symbol}`);
    
    if (!data || data.error) {
        analysisContent.innerHTML = `<div class="error">❌ 获取分析失败: ${data?.error || '未知错误'}</div>`;
        return;
    }
    
    const intervals = data.intervals || {};
    const intervalKeys = ['15m', '30m', '1h', '4h', '1d', '1w'];
    
    let html = '';
    for (const key of intervalKeys) {
        const intervalData = intervals[key];
        if (!intervalData) continue;
        if (intervalData.error) {
            html += `<div class="analysis-interval">
                <div class="interval-header">${intervalData.label || key}</div>
                <div class="interval-error">❌ ${intervalData.error}</div>
            </div>`;
            continue;
        }
        
        const trend = intervalData.trend || '--';
        const rsi = intervalData.rsi !== null && intervalData.rsi !== undefined ? intervalData.rsi.toFixed(1) : '--';
        const rsiSignal = intervalData.rsi_signal || '--';
        const volatility = intervalData.volatility !== null && intervalData.volatility !== undefined ? `${intervalData.volatility.toFixed(2)}%` : '--';
        const volumeRatio = intervalData.volume_ratio !== null && intervalData.volume_ratio !== undefined ? intervalData.volume_ratio.toFixed(2) : '--';
        const changePct = intervalData.change_pct !== null && intervalData.change_pct !== undefined ? `${intervalData.change_pct >= 0 ? '+' : ''}${intervalData.change_pct.toFixed(2)}%` : '--';
        const lastChange = intervalData.last_change_pct !== null && intervalData.last_change_pct !== undefined ? `${intervalData.last_change_pct >= 0 ? '+' : ''}${intervalData.last_change_pct.toFixed(2)}%` : '--';
        
        const supports = intervalData.supports && intervalData.supports.length > 0 
            ? intervalData.supports.map(s => `$${s.toFixed(4)}`).join(', ') 
            : '--';
        const resistances = intervalData.resistances && intervalData.resistances.length > 0 
            ? intervalData.resistances.map(r => `$${r.toFixed(4)}`).join(', ') 
            : '--';
        
        html += `<div class="analysis-interval">
            <div class="interval-header" onclick="toggleIntervalDetail(this)">
                <span class="interval-name">${intervalData.label || key}</span>
                <span class="interval-trend">${trend}</span>
                <span class="interval-toggle">▶</span>
            </div>
            <div class="interval-detail" style="display: none;">
                <table class="analysis-table">
                    <tr><td>RSI</td><td>${rsi}</td><td class="signal-cell">${rsiSignal}</td></tr>
                    <tr><td>涨跌幅</td><td>${changePct}</td><td></td></tr>
                    <tr><td>最新涨跌</td><td>${lastChange}</td><td></td></tr>
                    <tr><td>波动率</td><td>${volatility}</td><td></td></tr>
                    <tr><td>量比</td><td>${volumeRatio}</td><td></td></tr>
                    <tr><td>支撑位</td><td colspan="2">${supports}</td></tr>
                    <tr><td>阻力位</td><td colspan="2">${resistances}</td></tr>
                </table>
            </div>
        </div>`;
    }
    
    if (!html) {
        html = '<div class="empty">暂无分析数据</div>';
    }
    
    analysisContent.innerHTML = html;
}

function toggleIntervalDetail(header) {
    const detail = header.nextElementSibling;
    const toggle = header.querySelector('.interval-toggle');
    if (detail.style.display === 'none') {
        detail.style.display = 'block';
        toggle.textContent = '▼';
    } else {
        detail.style.display = 'none';
        toggle.textContent = '▶';
    }
}

// ========== 订单表格 ==========
function updateOrdersTable(bodyId, orders, side) {
    const tbody = document.getElementById(bodyId);
    if (!orders || orders.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty">暂无挂单</td></tr>';
        return;
    }
    
    tbody.innerHTML = orders.map(o => `
        <tr>
            <td>${o.price.toFixed(4)}</td>
            <td>${o.quantity.toFixed(6)}</td>
            <td>$${(o.price * o.quantity).toFixed(2)}</td>
            <td>${o.filled || 0}</td>
            <td>${o.status || '--'}</td>
        </tr>
    `).join('');
}

function updateTradesTable(trades) {
    const tbody = document.getElementById('tradesBody');
    if (!trades || trades.length === 0) {
        tbody.innerHTML = '<tr><td colspan="5" class="empty">暂无成交</td></tr>';
        return;
    }
    
    tbody.innerHTML = trades.slice(0, 20).map(t => `
        <tr>
            <td>${t.created_at || '--'}</td>
            <td><span class="side-${t.side}">${t.side === 'buy' ? '买入' : '卖出'}</span></td>
            <td>$${t.price.toFixed(4)}</td>
            <td>${t.quantity.toFixed(6)}</td>
            <td class="${t.profit > 0 ? 'profit' : t.profit < 0 ? 'loss' : ''}">${t.profit ? `$${t.profit.toFixed(4)}` : '--'}</td>
        </tr>
    `).join('');
}

// ========== 日志 ==========
function addLog(msg) {
    const logContainer = document.getElementById('logContainer');
    if (!logContainer) return;
    
    const time = new Date().toLocaleTimeString();
    const entry = document.createElement('div');
    entry.className = 'log-entry';
    entry.innerHTML = `<span class="log-time">[${time}]</span> ${msg}`;
    logContainer.appendChild(entry);
    logContainer.scrollTop = logContainer.scrollHeight;
    
    // 限制日志数量
    while (logContainer.children.length > 200) {
        logContainer.removeChild(logContainer.firstChild);
    }
}

// ========== 面板折叠 ==========
function togglePanel(header) {
    const content = header.nextElementSibling;
    const icon = header.querySelector('.toggle-icon');
    if (content.style.display === 'none') {
        content.style.display = 'block';
        icon.textContent = '▼';
    } else {
        content.style.display = 'none';
        icon.textContent = '▶';
    }
}

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

function toggleAdvicePanel() {
    const panel = document.getElementById('advicePanel');
    const icon = document.getElementById('adviceToggleIcon');
    if (panel.style.display === 'none') {
        panel.style.display = 'block';
        icon.textContent = '▼';
        // 展开时自动加载
        const symbol = document.getElementById('symbol').value;
        if (symbol) {
            adviceLoadedSymbol = '';
            loadAdvice();
        }
    } else {
        panel.style.display = 'none';
        icon.textContent = '▶';
    }
}

function toggleAnalysisPanel() {
    const panel = document.getElementById('analysisPanel');
    const icon = document.getElementById('analysisToggleIcon');
    if (panel.style.display === 'none') {
        panel.style.display = 'block';
        icon.textContent = '▼';
        // 展开时自动加载
        const symbol = document.getElementById('symbol').value;
        if (symbol) {
            analysisLoadedSymbol = '';
            loadAnalysis();
        }
    } else {
        panel.style.display = 'none';
        icon.textContent = '▶';
    }
}

function toggleBotConfig() {
    const panel = document.getElementById('botConfigBody');
    const icon = document.getElementById('botConfigToggle');
    if (panel.style.display === 'none') {
        panel.style.display = 'block';
        icon.textContent = '▼';
    } else {
        panel.style.display = 'none';
        icon.textContent = '▶';
    }
}

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

// ========== 格式化工具 ==========
function formatPrice(price) {
    if (!price || price <= 0) return '0.00';
    if (price >= 1000) return price.toFixed(2);
    if (price >= 1) return price.toFixed(4);
    if (price >= 0.01) return price.toFixed(6);
    return price.toFixed(8);
}

function formatQty(qty) {
    if (!qty || qty <= 0) return '0';
    if (qty >= 1000) return qty.toFixed(2);
    if (qty >= 1) return qty.toFixed(4);
    if (qty >= 0.01) return qty.toFixed(6);
    return qty.toFixed(8);
}

function formatDate(dateStr) {
    if (!dateStr) return '--';
    try {
        const d = new Date(dateStr);
        const month = (d.getMonth() + 1).toString().padStart(2, '0');
        const day = d.getDate().toString().padStart(2, '0');
        const hours = d.getHours().toString().padStart(2, '0');
        const minutes = d.getMinutes().toString().padStart(2, '0');
        return `${month}-${day} ${hours}:${minutes}`;
    } catch (e) {
        return dateStr;
    }
}
