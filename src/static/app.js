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
            // Token 过期，跳回登录页（只在 /app 页面跳转，避免死循环）
            if (window.location.pathname === '/app' || window.location.pathname === '/') {
                logout();
            }
        }
        return res;
    });
};

// ========== 初始化 ==========
document.addEventListener('DOMContentLoaded', () => {
    // 显示用户名
    const username = getUsername();
    if (username) {
        document.getElementById('headerUsername').textContent = '👤 ' + username;
    }
    
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
        // 切换币种后自动加载分析和建议
        const symbol = document.getElementById('symbol').value;
        if (symbol) {
            adviceLoadedSymbol = symbol;
            analysisLoadedSymbol = symbol;
            // 自动展开建议面板并加载
            document.getElementById('advicePanel').style.display = 'block';
            document.getElementById('adviceToggleIcon').textContent = '▼';
            loadAdvice();
            // 自动展开分析面板并加载
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
        document.getElementById('symbolPrice').textContent = `$${formatPrice(data.price)}`;
        document.getElementById('currentPrice').textContent = `$${formatPrice(data.price)}`;
        
        if (data.high_24h && data.low_24h && data.high_24h > 0 && data.low_24h > 0) {
            const volatility = ((data.high_24h - data.low_24h) / data.low_24h) * 100;
            document.getElementById('volatility').textContent = `${volatility.toFixed(2)}%`;
            document.getElementById('optimalRange').textContent = 
                `$${formatPrice(data.low_24h)} ~ $${formatPrice(data.high_24h)}`;
            
            const feeRate = parseFloat(document.getElementById('feeRate').value) || 0.1;
            const minSpacing = feeRate * 2;  // 最低不能低于手续费合计的 2 倍
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
    if (!price || price <= 0) {
        // 价格还没加载时，显示等待状态
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
    
    // 单向挂单模式：
    // - 当前价格下方挂 N 个买单（只挂买单）
    // - 当前价格上方挂 N 个卖单（只挂卖单）
    // - 3/4 资金挂单，1/4 预留缓冲
    const halfGrids = Math.floor(gridCount / 2);
    const buyGrids = Math.max(1, halfGrids);  // 买单格数（下方）
    const sellGrids = Math.max(1, halfGrids); // 卖单格数（上方）
    
    // 资金分配：3/4 挂单，1/4 预留
    const investRatio = 0.75;
    const reserveRatio = 0.25;
    
    // 互动计算：输入 USDT 自动算币，输入币自动算 USDT
    let perGridUsdt = 0;
    let perGridQty = 0;
    let buyCoinUsdt = 0;  // 需要用来买币的 USDT 金额
    let estimatedCoin = 0;  // 根据 USDT 估算的币数量
    let estimatedUsdt = 0;  // 根据币数量估算的 USDT
    
    if (usdtAmount > 0 && coinAmount > 0) {
        // 两者都填：分别计算
        const usdtPerGrid = (usdtAmount * investRatio) / buyGrids;
        const coinPerGrid = (coinAmount * investRatio) / sellGrids;
        perGridUsdt = usdtPerGrid;
        perGridQty = coinPerGrid;
        buyCoinUsdt = 0;  // 已有币，不需要买
    } else if (usdtAmount > 0) {
        // 只填了 USDT：75% 用于挂单，其中一半挂买单，一半买币挂卖单
        const investUsdt = usdtAmount * investRatio;  // 75% 用于挂单
        const buyOrderUsdt = investUsdt / 2;  // 一半挂买单
        const sellOrderUsdt = investUsdt / 2;  // 一半买币挂卖单
        perGridUsdt = buyOrderUsdt / buyGrids;
        perGridQty = (sellOrderUsdt / price) / sellGrids;
        buyCoinUsdt = sellOrderUsdt;  // 需要买币的金额
        
        // 估算需要的总币数量（卖单所需币 + 预留）
        estimatedCoin = perGridQty * sellGrids + perGridQty;  // 挂单 + 1格预留
    } else if (coinAmount > 0) {
        // 只填了币数量
        perGridQty = (coinAmount * investRatio) / sellGrids;
        perGridUsdt = perGridQty * price;
        buyCoinUsdt = 0;
        
        // 估算需要的总 USDT（买单所需 USDT + 预留）
        estimatedUsdt = perGridUsdt * buyGrids + perGridUsdt;  // 挂单 + 1格预留
        // 实际总投资 = 估算 USDT / 0.75 * 2（因为买单+卖单各一半）
        estimatedUsdt = (perGridUsdt * buyGrids + perGridUsdt) * 2;  // 买单+卖单+预留
    }
    
    // 显示互动估算值
    const usdtEstimateEl = document.getElementById('usdtEstimate');
    if (usdtAmount > 0 && estimatedCoin > 0) {
        usdtEstimateEl.textContent = `≈ ${formatQty(estimatedCoin)} 币`;
    } else {
        usdtEstimateEl.textContent = '';
    }
    
    // 计算实际挂单金额
    const orderUsdt = perGridUsdt * buyGrids;  // 买单挂单金额
    const orderCoin = perGridQty * sellGrids;  // 卖单所需币量
    
    // 预留缓冲（1/4 资金）
    const reservedUsdt = usdtAmount * reserveRatio;
    const reservedCoin = coinAmount * reserveRatio;
    
    // 总 USDT 需求 = 买单挂单 + 买币金额 + 预留
    const totalUsdtNeeded = orderUsdt + buyCoinUsdt + reservedUsdt;
    // 总币需求 = 卖单所需币 + 预留
    const totalCoinNeeded = orderCoin + reservedCoin;
    
    // 最小交易金额检查（Gate.io 最小挂单金额为 1 USDT）
    const MIN_ORDER_USDT = 1.0;
    const perGridUsdtBuy = perGridUsdt;  // 买单每格 USDT
    const perGridUsdtSell = perGridQty * price;  // 卖单每格 USDT 价值
    const minOrderOk = perGridUsdtBuy >= MIN_ORDER_USDT && perGridUsdtSell >= MIN_ORDER_USDT;
    
    // 显示每格金额
    document.getElementById('perGridAmount').textContent = 
        `$${perGridUsdt.toFixed(2)} / ${formatQty(perGridQty)} 币`;
    
    // 最小交易金额提示
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
    
    // USDT 充足性检查
    const usdtHint = document.getElementById('usdtMinHint');
    if (usdtAmount > 0) {
        if (usdtAmount >= totalUsdtNeeded) {
            usdtHint.textContent = `✅ 足够 (买单: $${orderUsdt.toFixed(2)}, 买币: $${buyCoinUsdt.toFixed(2)}, 预留: $${reservedUsdt.toFixed(2)})`;
            usdtHint.className = 'hint-text valid';
        } else if (usdtAmount >= orderUsdt + buyCoinUsdt) {
            usdtHint.textContent = `⚠️ 仅够挂单 (建议预留 1/4: $${reservedUsdt.toFixed(2)})`;
            usdtHint.className = 'hint-text warning';
        } else {
            usdtHint.textContent = `❌ 不足 (最少 $${totalUsdtNeeded.toFixed(2)})`;
            usdtHint.className = 'hint-text invalid';
        }
    } else {
        usdtHint.textContent = '';
    }
    
    // 币充足性检查
    const coinHint = document.getElementById('coinMinHint');
    if (coinAmount > 0) {
        if (coinAmount >= totalCoinNeeded) {
            coinHint.textContent = `✅ 足够 (挂单: ${formatQty(orderCoin)}, 预留: ${formatQty(reservedCoin)})`;
            coinHint.className = 'hint-text valid';
        } else if (coinAmount >= orderCoin) {
            coinHint.textContent = `⚠️ 仅够挂单 (建议预留 1/4: ${formatQty(reservedCoin)})`;
            coinHint.className = 'hint-text warning';
        } else {
            coinHint.textContent = `❌ 不足 (最少 ${formatQty(totalCoinNeeded)})`;
            coinHint.className = 'hint-text invalid';
        }
    } else {
        coinHint.textContent = '';
    }
    
    // 需要额外购买的币
    let needBuy = 0;
    let needBuyUsdt = 0;
    
    if (coinAmount > 0) {
        // 用户填了币数量：检查已有币是否足够
        if (currentBalance.coin < totalCoinNeeded) {
            needBuy = totalCoinNeeded - currentBalance.coin;
            needBuyUsdt = needBuy * price;
        }
    } else if (usdtAmount > 0) {
        // 只填 USDT：需要买币来挂卖单
        const needCoinTotal = orderCoin + reservedCoin;
        if (currentBalance.coin < needCoinTotal) {
            needBuy = needCoinTotal - currentBalance.coin;
            needBuyUsdt = needBuy * price;
        }
    }
    
    document.getElementById('needBuyCoin').textContent = 
        needBuy > 0.000001 
            ? `需要 ${formatQty(needBuy)} 币 (≈ $${needBuyUsdt.toFixed(2)})` 
            : '✅ 已足够';
    
    // 单格收益率
    const spacingPct = spacing / 100;
    const totalFeePct = (feeRate / 100) * 2;  // 买卖合计手续费
    const profitPct = spacingPct - totalFeePct;  // 净收益率
    const profitPerTrade = profitPct * perGridUsdt;
    
    // 显示净收益率
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
    
    // 网格间距（美元）
    const gridSpacing = price * spacingPct;
    document.getElementById('priceSpacingHint').textContent = `价格间隔: $${formatPrice(gridSpacing)}`;
    
    // 启动按钮状态
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
    
    // 更新顶部信息栏
    if (data.strategy) {
        const s = data.strategy;
        
        // 当前价格
        if (s.current_price) {
            document.getElementById('currentPrice').textContent = `$${formatPrice(s.current_price)}`;
        }
        
        // 总收益 (USDT) - 含百分比
        const totalProfit = data.total_profit || 0;
        const totalInvestment = data.config?.total_investment || 0;
        const totalProfitEl = document.getElementById('totalProfit');
        const profitPct = totalInvestment > 0 ? (totalProfit / totalInvestment) * 100 : 0;
        totalProfitEl.textContent = `${totalProfit.toFixed(2)} ${profitPct >= 0 ? '+' : ''}${profitPct.toFixed(2)}%`;
        totalProfitEl.className = `info-value ${totalProfit >= 0 ? 'profit' : 'loss'}`;
        
        // 网格收益 (USDT)
        const gridProfit = data.grid_profit || 0;
        const gridProfitEl = document.getElementById('gridProfit');
        gridProfitEl.textContent = `${gridProfit >= 0 ? '+' : ''}${gridProfit.toFixed(2)}`;
        gridProfitEl.className = `info-value ${gridProfit >= 0 ? 'profit' : 'loss'}`;
        
        // 浮动盈亏 (USDT)
        const floatingPnl = data.floating_pnl || 0;
        const floatingPnlEl = document.getElementById('floatingPnl');
        floatingPnlEl.textContent = `${floatingPnl >= 0 ? '+' : ''}${floatingPnl.toFixed(2)}`;
        floatingPnlEl.className = `info-value ${floatingPnl >= 0 ? 'profit' : 'loss'}`;
        
        // 年化收益率
        const annualized = data.annualized_return || 0;
        const annualizedEl = document.getElementById('annualizedReturn');
        annualizedEl.textContent = `${annualized >= 0 ? '+' : ''}${annualized.toFixed(2)}%`;
        annualizedEl.className = `info-value ${annualized >= 0 ? 'profit' : 'loss'}`;
        
        // 总投资 (USDT)
        document.getElementById('totalInvestment').textContent = totalInvestment.toFixed(2);
        
        // 交易次数
        document.getElementById('totalTrades').textContent = s.total_trades || 0;
        
        // 运行时长
        document.getElementById('runningTimeDisplay').textContent = data.running_time || '--';
        
        // 价格区间 (USDT)
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
// 智能格式化价格：根据数值大小自动调整小数位数
function formatPrice(value) {
    if (!value || value <= 0) return '--';
    if (value >= 1) return value.toFixed(4);
    if (value >= 0.001) return value.toFixed(6);
    if (value >= 0.000001) return value.toFixed(8);
    if (value >= 0.00000001) return value.toFixed(10);
    return value.toFixed(12);
}

// 智能格式化数量：根据数值大小自动调整小数位数
function formatQty(value) {
    if (!value || value <= 0) return '0';
    if (value >= 1) return value.toFixed(4);
    if (value >= 0.001) return value.toFixed(6);
    if (value >= 0.000001) return value.toFixed(8);
    if (value >= 0.00000001) return value.toFixed(10);
    return value.toFixed(12);
}

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

// ========== 技术分析面板 ==========
let analysisData = null;
let currentAnalysisInterval = '15m';

function toggleAnalysisPanel() {
    const panel = document.getElementById('analysisPanel');
    const icon = document.getElementById('analysisToggleIcon');
    if (panel.style.display === 'none') {
        panel.style.display = 'block';
        icon.textContent = '▼';
        // 只有币种变了才重新加载
        const symbol = document.getElementById('symbol').value;
        if (analysisLoadedSymbol !== symbol) {
            analysisLoadedSymbol = symbol;
            loadAnalysis();
        }
    } else {
        panel.style.display = 'none';
        icon.textContent = '▶';
    }
}

async function loadAnalysis() {
    const exchange = document.getElementById('exchange').value;
    const symbol = document.getElementById('symbol').value;
    
    if (!symbol) {
        document.getElementById('analysisLoading').style.display = 'block';
        document.getElementById('analysisLoading').textContent = '请先选择币种';
        document.getElementById('analysisContent').style.display = 'none';
        document.getElementById('analysisError').style.display = 'none';
        return;
    }
    
    document.getElementById('analysisLoading').style.display = 'block';
    document.getElementById('analysisLoading').textContent = '正在获取技术分析数据...';
    document.getElementById('analysisContent').style.display = 'none';
    document.getElementById('analysisError').style.display = 'none';
    
    const data = await apiGet(`/api/analysis?exchange=${exchange}&symbol=${symbol}`);
    
    document.getElementById('analysisLoading').style.display = 'none';
    
    if (data && data.intervals && Object.keys(data.intervals).length > 0) {
        analysisData = data;
        document.getElementById('analysisContent').style.display = 'block';
        document.getElementById('analysisError').style.display = 'none';
        
        // 默认选中第一个有数据的周期
        const intervals = Object.keys(data.intervals);
        if (intervals.length > 0) {
            currentAnalysisInterval = intervals[0];
            renderAnalysisTab(currentAnalysisInterval);
        }
    } else if (data && data.error) {
        document.getElementById('analysisError').style.display = 'block';
        document.getElementById('analysisError').textContent = `❌ 分析失败: ${data.error}`;
        document.getElementById('analysisContent').style.display = 'none';
    } else {
        document.getElementById('analysisError').style.display = 'block';
        document.getElementById('analysisError').textContent = '❌ 无法获取分析数据';
        document.getElementById('analysisContent').style.display = 'none';
    }
}

function switchAnalysisTab(interval) {
    currentAnalysisInterval = interval;
    
    // 更新 Tab 高亮
    document.querySelectorAll('.analysis-tab').forEach(tab => {
        tab.classList.toggle('active', tab.dataset.interval === interval);
    });
    
    renderAnalysisTab(interval);
}

function renderAnalysisTab(interval) {
    if (!analysisData || !analysisData.intervals[interval]) {
        document.getElementById('analysisIntervalContent').innerHTML = 
            '<div class="analysis-error">该周期无数据</div>';
        return;
    }
    
    const data = analysisData.intervals[interval];
    if (data.error) {
        document.getElementById('analysisIntervalContent').innerHTML = 
            `<div class="analysis-error">❌ ${data.error}</div>`;
        return;
    }
    
    const currentPrice = analysisData.current_price || data.current_price || 0;
    const changeClass = data.change_pct >= 0 ? 'up' : 'down';
    const lastChangeClass = data.last_change_pct >= 0 ? 'up' : 'down';
    
    // 构建均线 HTML
    let maHtml = '';
    const mas = [
        { label: 'MA5', value: data.ma5 },
        { label: 'MA10', value: data.ma10 },
        { label: 'MA20', value: data.ma20 },
        { label: 'MA60', value: data.ma60 }
    ];
    mas.forEach(ma => {
        if (ma.value !== null && ma.value !== undefined) {
            const color = ma.value > currentPrice ? '#f44336' : '#4caf50';
            maHtml += `<span class="ma-item"><span class="ma-label">${ma.label}:</span><span style="color:${color}">$${formatPrice(ma.value)}</span></span>`;
        }
    });
    
    // 构建支撑阻力 HTML
    let srHtml = '';
    if (data.supports && data.supports.length > 0) {
        data.supports.forEach(s => {
            srHtml += `<span class="sr-item support">🟢 支撑 $${formatPrice(s)}</span>`;
        });
    }
    if (data.resistances && data.resistances.length > 0) {
        data.resistances.forEach(r => {
            srHtml += `<span class="sr-item resistance">🔴 阻力 $${formatPrice(r)}</span>`;
        });
    }
    if (!srHtml) srHtml = '<span style="color:#8899aa;font-size:13px;">暂无数据</span>';
    
    // 构建资金流量 HTML
    const mf = data.money_flow || {};
    const flowClass = mf.net_flow >= 0 ? 'positive' : 'negative';
    const flowRatio = mf.flow_ratio ? (mf.flow_ratio * 100).toFixed(1) : '--';
    
    // 成交量柱状图
    const volumeRatio = data.volume_ratio || 0;
    const volumeBarWidth = Math.min(volumeRatio * 50, 100);  // 最大 100%
    
    const html = `
        <div class="analysis-grid">
            <!-- 当前价格 & 涨跌幅 -->
            <div class="analysis-card">
                <div class="analysis-card-title">当前价格</div>
                <div class="analysis-card-value">$${formatPrice(currentPrice)}</div>
            </div>
            <div class="analysis-card">
                <div class="analysis-card-title">周期涨跌幅</div>
                <div class="analysis-card-value ${changeClass}">${data.change_pct >= 0 ? '+' : ''}${data.change_pct}%</div>
            </div>
            <div class="analysis-card">
                <div class="analysis-card-title">最新 K 线涨跌</div>
                <div class="analysis-card-value ${lastChangeClass}">${data.last_change_pct >= 0 ? '+' : ''}${data.last_change_pct}%</div>
            </div>
            <div class="analysis-card">
                <div class="analysis-card-title">周期最高 / 最低</div>
                <div class="analysis-card-value" style="font-size:14px;">
                    <span style="color:#f44336;">H: $${formatPrice(data.high)}</span> / 
                    <span style="color:#4caf50;">L: $${formatPrice(data.low)}</span>
                </div>
            </div>
            
            <!-- 趋势 -->
            <div class="analysis-card full-width">
                <div class="analysis-card-title">趋势判断</div>
                <div class="analysis-card-value" style="font-size:15px;">${data.trend || '数据不足'}</div>
            </div>
            
            <!-- 均线 -->
            <div class="analysis-card full-width">
                <div class="analysis-card-title">均线系统</div>
                <div class="ma-row">${maHtml || '<span style="color:#8899aa;font-size:13px;">数据不足</span>'}</div>
            </div>
            
            <!-- RSI -->
            <div class="analysis-card">
                <div class="analysis-card-title">RSI (14)</div>
                <div class="analysis-card-value" style="font-size:18px; ${data.rsi >= 70 ? 'color:#f44336;' : data.rsi <= 30 ? 'color:#4caf50;' : ''}">${data.rsi !== null && data.rsi !== undefined ? data.rsi.toFixed(1) : '--'}</div>
                <div style="font-size:11px;color:#8899aa;margin-top:2px;">${data.rsi_signal || ''}</div>
            </div>
            <div class="analysis-card">
                <div class="analysis-card-title">波动率</div>
                <div class="analysis-card-value">${data.volatility !== null && data.volatility !== undefined ? data.volatility + '%' : '--'}</div>
            </div>
            
            <!-- 支撑阻力 -->
            <div class="analysis-card full-width">
                <div class="analysis-card-title">支撑位 / 阻力位</div>
                <div class="sr-row">${srHtml}</div>
            </div>
            
            <!-- 成交量 -->
            <div class="analysis-card">
                <div class="analysis-card-title">成交量</div>
                <div class="volume-bar">
                    <span style="font-size:13px;color:#e0e0e0;">${data.volume ? formatQty(data.volume) : '--'}</span>
                    <div style="flex:1;background:#1a2a3a;border-radius:3px;height:6px;">
                        <div class="volume-bar-fill" style="width:${volumeBarWidth}%;"></div>
                    </div>
                </div>
                <div style="font-size:11px;color:#8899aa;margin-top:2px;">
                    均值: ${data.avg_volume ? formatQty(data.avg_volume) : '--'} 
                    | 量比: ${data.volume_ratio ? data.volume_ratio + 'x' : '--'}
                </div>
            </div>
            <div class="analysis-card">
                <div class="analysis-card-title">数据点数</div>
                <div class="analysis-card-value" style="font-size:14px;">${data.data_points || 0}</div>
            </div>
            
            <!-- 资金流量 -->
            <div class="analysis-card full-width">
                <div class="analysis-card-title">资金流量估算</div>
                <div class="flow-row">
                    <span class="flow-item ${flowClass}">
                        <span class="flow-label">净流量:</span>
                        ${mf.net_flow >= 0 ? '+' : ''}${mf.net_flow || 0}
                    </span>
                    <span class="flow-item positive">
                        <span class="flow-label">流入:</span>
                        ${mf.inflow || 0}
                    </span>
                    <span class="flow-item negative">
                        <span class="flow-label">流出:</span>
                        ${mf.outflow || 0}
                    </span>
                    <span class="flow-item">
                        <span class="flow-label">流入占比:</span>
                        ${flowRatio}%
                    </span>
                </div>
            </div>
        </div>
    `;
    
    document.getElementById('analysisIntervalContent').innerHTML = html;
}


// ========== 交易建议面板 ==========
function toggleAdvicePanel() {
    const panel = document.getElementById('advicePanel');
    const icon = document.getElementById('adviceToggleIcon');
    if (panel.style.display === 'none') {
        panel.style.display = 'block';
        icon.textContent = '▼';
        // 只有币种变了才重新加载
        const symbol = document.getElementById('symbol').value;
        if (adviceLoadedSymbol !== symbol) {
            adviceLoadedSymbol = symbol;
            loadAdvice();
        }
    } else {
        panel.style.display = 'none';
        icon.textContent = '▶';
    }
}

async function loadAdvice() {
    const exchange = document.getElementById('exchange').value;
    const symbol = document.getElementById('symbol').value;
    
    if (!symbol) {
        document.getElementById('adviceLoading').style.display = 'block';
        document.getElementById('adviceLoading').textContent = '请先选择币种';
        document.getElementById('adviceContent').style.display = 'none';
        document.getElementById('adviceError').style.display = 'none';
        return;
    }
    
    document.getElementById('adviceLoading').style.display = 'block';
    document.getElementById('adviceLoading').textContent = '正在分析市场数据...';
    document.getElementById('adviceContent').style.display = 'none';
    document.getElementById('adviceError').style.display = 'none';
    
    // 同时加载建议和分析数据
    const [adviceData, analysisResult] = await Promise.all([
        apiGet(`/api/advice?exchange=${exchange}&symbol=${symbol}`),
        apiGet(`/api/analysis?exchange=${exchange}&symbol=${symbol}`)
    ]);
    
    document.getElementById('adviceLoading').style.display = 'none';
    
    if (adviceData && adviceData.score !== undefined) {
        document.getElementById('adviceContent').style.display = 'block';
        document.getElementById('adviceError').style.display = 'none';
        
        // 保存分析数据到全局变量，供 renderAdvice 使用
        if (analysisResult && analysisResult.intervals) {
            window.analysisData = analysisResult;
        }
        
        renderAdvice(adviceData);
    } else if (adviceData && adviceData.error) {
        document.getElementById('adviceError').style.display = 'block';
        document.getElementById('adviceError').textContent = `❌ ${adviceData.error}`;
        document.getElementById('adviceContent').style.display = 'none';
    } else {
        document.getElementById('adviceError').style.display = 'block';
        document.getElementById('adviceError').textContent = '❌ 无法获取交易建议';
        document.getElementById('adviceContent').style.display = 'none';
    }
}

function renderAdvice(data) {
    // 评分圆圈
    const score = data.score || 0;
    const scoreCircle = document.getElementById('adviceScoreCircle');
    const scoreValue = document.getElementById('adviceScoreValue');
    scoreValue.textContent = score;
    
    // 根据分数设置颜色
    let scoreColor, scoreBg;
    if (score >= 70) {
        scoreColor = '#4caf50';
        scoreBg = 'rgba(76, 175, 80, 0.15)';
    } else if (score >= 50) {
        scoreColor = '#ff9800';
        scoreBg = 'rgba(255, 152, 0, 0.15)';
    } else if (score >= 30) {
        scoreColor = '#ff5722';
        scoreBg = 'rgba(255, 87, 34, 0.15)';
    } else {
        scoreColor = '#f44336';
        scoreBg = 'rgba(244, 67, 54, 0.15)';
    }
    scoreCircle.style.background = `conic-gradient(${scoreColor} ${score * 3.6}deg, ${scoreBg} ${score * 3.6}deg)`;
    
    // 总结
    const summaryEl = document.getElementById('adviceSummary');
    summaryEl.textContent = data.summary || '--';
    summaryEl.style.color = scoreColor;
    
    // 详情
    document.getElementById('adviceDetail').textContent = data.detail || '';
    
    // 操作建议
    const suggestionsEl = document.getElementById('adviceSuggestions');
    if (data.suggestions && data.suggestions.length > 0) {
        suggestionsEl.innerHTML = data.suggestions.map(s => `<li>${s}</li>`).join('');
    } else {
        suggestionsEl.innerHTML = '<li style="color:#8899aa;">暂无建议</li>';
    }
    
    // 风险提示
    const warningsEl = document.getElementById('adviceWarnings');
    if (data.risk_warnings && data.risk_warnings.length > 0) {
        warningsEl.innerHTML = data.risk_warnings.map(w => `<li>${w}</li>`).join('');
    } else {
        warningsEl.innerHTML = '<li style="color:#8899aa;">暂无风险提示</li>';
    }
    
    // 分析依据
    const reasonsEl = document.getElementById('adviceReasons');
    if (data.reasons && data.reasons.length > 0) {
        reasonsEl.innerHTML = data.reasons.map(r => `<li>${r}</li>`).join('');
    } else {
        reasonsEl.innerHTML = '<li style="color:#8899aa;">暂无分析依据</li>';
    }
    
    // 价格信息
    document.getElementById('adviceCurrentPrice').textContent = data.current_price ? `$${formatPrice(data.current_price)}` : '--';
    document.getElementById('adviceRecentLow').textContent = data.recent_low ? `$${formatPrice(data.recent_low)}` : '--';
    document.getElementById('adviceRecentHigh').textContent = data.recent_high ? `$${formatPrice(data.recent_high)}` : '--';
    
    // 关键数据概览 - 从 analysisData 中提取各周期关键指标
    if (window.analysisData && window.analysisData.intervals) {
        const intervals = window.analysisData.intervals;
        const intervalKeys = Object.keys(intervals);
        
        // 构建关键数据表格
        let tableHtml = `<div class="advice-data-table">
            <table>
                <thead>
                    <tr>
                        <th>周期</th>
                        <th>趋势</th>
                        <th>RSI</th>
                        <th>MA5</th>
                        <th>MA20</th>
                        <th>波动率</th>
                        <th>量比</th>
                    </tr>
                </thead>
                <tbody>`;
        
        intervalKeys.forEach(key => {
            const d = intervals[key];
            if (!d || d.error) return;
            
            const trendIcon = d.trend ? d.trend.charAt(0) : '➡️';
            const trendClass = trendIcon === '📈' ? 'up' : (trendIcon === '📉' ? 'down' : 'neutral');
            const rsiClass = d.rsi >= 70 ? 'down' : (d.rsi <= 30 ? 'up' : '');
            
            tableHtml += `<tr>
                <td><strong>${d.label || key}</strong></td>
                <td class="${trendClass}">${trendIcon}</td>
                <td class="${rsiClass}">${d.rsi !== null && d.rsi !== undefined ? d.rsi.toFixed(1) : '--'}</td>
                <td>${d.ma5 ? '$' + formatPrice(d.ma5) : '--'}</td>
                <td>${d.ma20 ? '$' + formatPrice(d.ma20) : '--'}</td>
                <td>${d.volatility !== null && d.volatility !== undefined ? d.volatility + '%' : '--'}</td>
                <td>${d.volume_ratio ? d.volume_ratio + 'x' : '--'}</td>
            </tr>`;
        });
        
        tableHtml += `</tbody></table></div>`;
        
        // 插入到价格信息后面
        const priceInfo = document.querySelector('.advice-price-info');
        // 移除旧的表格
        const oldTable = document.querySelector('.advice-data-table');
        if (oldTable) oldTable.remove();
        priceInfo.insertAdjacentHTML('afterend', tableHtml);
    }
}
