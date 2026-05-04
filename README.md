# GRID-Pro - 自动网格交易套利程序

> 📅 2026-05-04 更新：优化 Web UI 界面，新增顶部信息栏完整数据展示、USDT/币数量互动计算、最小交易金额检查等功能。

支持 Binance 和 Gate.io 等交易所的现货网格策略，在震荡行情中自动低买高卖套利。

## 功能特点

### 核心功能
- **多交易所支持**：支持 Binance、Gate.io 等主流交易所
- **现货网格**：在设定的价格区间内自动低买高卖
- **自动网格调整**：根据实时行情自动移动网格，确保不会无买单或无卖单
- **智能风控**：资金管理、止损止盈、交易频率控制

### 网格策略
- 在价格区间内均匀分布网格层级
- 每个网格层同时挂买单和卖单
- 成交后自动在相邻网格补单
- 根据实时行情自动调整网格位置

### 自动调整机制
- 定期检查当前价格与网格中心的偏离程度
- 当偏离超过阈值时，整体移动网格范围
- 取消旧订单并重新计算网格位置
- 确保始终有合理的买单和卖单在挂单中

### 风险管理
- 资金余额检查
- 止损/止盈自动触发
- 每日交易次数限制
- 交易频率控制

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置

编辑 `config/config.json` 文件：

```json
{
  "exchanges": [
    {
      "name": "binance",
      "api_key": "YOUR_API_KEY",
      "api_secret": "YOUR_API_SECRET",
      "enable": true
    }
  ],
  "grids": [
    {
      "symbol": "BTC/USDT",
      "upper_price": "70000",
      "lower_price": "50000",
      "grid_count": 10,
      "total_investment": "1000",
      "auto_adjust_enabled": true,
      "adjust_interval": 300,
      "price_deviation_threshold": "0.02"
    }
  ]
}
```

### 3. 运行

```bash
python -m src.grid_bot
```

或指定配置文件路径：

```bash
python -m src.grid_bot config/my_config.json
```

## 配置说明

### 交易所配置

| 参数 | 说明 | 示例 |
|------|------|------|
| `name` | 交易所名称 | `binance`, `gateio` |
| `api_key` | API密钥 | - |
| `api_secret` | API密钥 | - |
| `enable` | 是否启用 | `true`/`false` |

### 网格策略配置

| 参数 | 说明 | 默认值 |
|------|------|--------|
| `symbol` | 交易对 | `BTC/USDT` |
| `upper_price` | 网格上限价格 | `70000` |
| `lower_price` | 网格下限价格 | `50000` |
| `grid_count` | 网格数量 | `10` |
| `total_investment` | 总投资额(USDT) | `1000` |
| `auto_adjust_enabled` | 启用自动调整 | `true` |
| `adjust_interval` | 调整间隔(秒) | `300` |
| `price_deviation_threshold` | 价格偏离阈值 | `0.02` (2%) |
| `max_open_orders` | 最大同时挂单数 | `5` |
| `stop_loss_pct` | 止损百分比 | `0.05` (5%) |
| `take_profit_pct` | 止盈百分比 | `0.10` (10%) |

## 项目结构

```
grid-trading-bot/
├── config/
│   └── config.json          # 配置文件
├── src/
│   ├── __init__.py
│   ├── grid_bot.py          # 主程序入口
│   ├── exchanges/
│   │   ├── __init__.py
│   │   ├── base.py          # 交易所基类
│   │   ├── binance.py       # Binance 实现
│   │   ├── gateio.py        # Gate.io 实现
│   │   └── factory.py       # 交易所工厂
│   ├── strategies/
│   │   ├── __init__.py
│   │   └── grid_strategy.py # 网格策略核心
│   ├── models/
│   │   ├── __init__.py
│   │   ├── config.py        # 配置模型
│   │   └── trading.py       # 交易模型
│   └── utils/
│       ├── __init__.py
│       ├── config_loader.py # 配置加载
│       └── risk_manager.py  # 风险管理
├── requirements.txt
└── README.md
```

## 策略原理

### 网格策略

1. **网格建立**：在设定的价格区间内，将价格均匀分为 N 个网格
2. **挂单策略**：在当前价格下方挂买单，上方挂卖单
3. **成交补单**：买单成交后，在更低价格重新挂买单；卖单成交后，在更高价格重新挂卖单
4. **套利原理**：每次低买高卖赚取网格差价

### 自动调整

1. 定期检查当前价格与网格中心的偏离度
2. 如果价格大幅偏离网格中心，整体移动网格范围
3. 确保网格始终围绕当前价格，避免只有买单或只有卖单的情况

## 安全提示

- **请勿将 API 密钥提交到版本控制系统**
- 建议使用只读权限或仅交易权限的 API 密钥
- 首次使用建议先用小金额测试
- 定期检查机器人运行状态

## 免责声明

**风险警告**：加密货币交易存在高风险。本程序仅供学习和研究使用，使用本程序进行实盘交易产生的任何损失由使用者自行承担。

## License

MIT
