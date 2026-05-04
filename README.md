# GRID-Pro - 自动网格交易系统

> 📅 2026-05-04 更新：新增 Web 管理界面、用户登录、技术分析、网格持久化（服务重启自动恢复）

支持 Binance 和 Gate.io 等交易所的现货网格策略，在震荡行情中自动低买高卖套利。

## 功能特点

### 核心功能
- **Web 管理界面**：浏览器操作，无需命令行
- **多交易所支持**：支持 Binance、Gate.io
- **现货网格**：在设定的价格区间内自动低买高卖
- **自动网格调整**：根据实时行情自动移动网格
- **智能风控**：资金管理、止损止盈
- **网格持久化**：服务重启后自动从交易所拉取挂单恢复运行
- **技术分析**：多周期 K 线分析、RSI、均线、支撑阻力位
- **交易建议**：综合评分系统，给出操作建议

### 网格策略
- 在价格区间内均匀分布网格层级
- 每个网格层同时挂买单和卖单
- 成交后自动在相邻网格补单
- 根据实时行情自动调整网格位置

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

通过 Web 页面配置（推荐）：
1. 启动服务后访问 `http://localhost:3000`
2. 注册账号并登录
3. 在页面中填写交易所 API Key（Secret 加密存储）

或者编辑 `.env` 文件：

```bash
# 从模板创建
cp .env.example .env

# 编辑 .env 文件填入你的 API Key
nano .env
```

`.env` 文件格式：
```
BINANCE_API_KEY=your_binance_api_key
BINANCE_API_SECRET=your_binance_api_secret
GATEIO_API_KEY=your_gateio_api_key
GATEIO_API_SECRET=your_gateio_api_secret
```

> ⚠️ **安全提示**：API Secret 会加密存储，建议使用仅交易权限的 API Key

### 3. 启动服务

```bash
# 后台启动（推荐）
./run.sh

# 查看日志
tail -f logs/server.log

# 停止服务
./run.sh stop

# 重启服务
./run.sh restart
```

启动后访问 **http://localhost:3000** 进入 Web 管理界面。

### 4. 使用 Web 界面

1. 注册账号并登录
2. 在左侧面板配置交易所 API Key 并测试连接
3. 选择币种，系统会自动加载价格和技术分析
4. 设置网格参数（价格间隔、网格数量、USDT 金额等）
5. 点击"启动网格"开始自动交易

## 项目结构

```
GRID-Pro/
├── config/
│   └── config.json          # 网格策略配置文件
├── data/                    # 数据库文件
├── logs/                    # 日志文件
│   ├── server.log           # Web 服务日志
│   └── grid_bot_*.log       # 程序运行日志
├── src/
│   ├── web_api.py           # Web API 服务（FastAPI）
│   ├── grid_bot.py          # 命令行主程序
│   ├── exchanges/
│   │   ├── base.py          # 交易所基类
│   │   ├── binance.py       # Binance 实现
│   │   ├── gateio.py        # Gate.io 实现
│   │   └── factory.py       # 交易所工厂
│   ├── strategies/
│   │   └── grid_strategy.py # 网格策略核心
│   ├── analysis/
│   │   └── market_analyzer.py # 技术分析
│   ├── models/
│   │   ├── config.py        # 配置模型
│   │   └── trading.py       # 交易模型
│   ├── static/              # 前端页面
│   │   ├── index.html       # 主页面
│   │   ├── login.html       # 登录页面
│   │   ├── app.js           # 前端逻辑
│   │   └── style.css        # 样式
│   └── utils/
│       ├── config_loader.py # 配置加载
│       └── risk_manager.py  # 风险管理
├── .env                     # API Key 配置（加密存储）
├── run.sh                   # 一键启动/停止脚本
└── requirements.txt
```

## 常用命令

```bash
# 启动服务（后台运行）
./run.sh

# 查看实时日志
tail -f logs/server.log

# 查看程序运行日志
tail -f logs/grid_bot_$(date +%Y-%m-%d).log

# 停止服务
./run.sh stop

# 重启服务
./run.sh restart

# 命令行模式（不启动 Web 界面）
python -m src.grid_bot
```

## 安全提示

- **请勿将 API 密钥提交到版本控制系统**
- API Secret 通过 Fernet 加密后存储在 `.env` 文件中
- 建议使用仅交易权限的 API Key
- 首次使用建议先用小金额测试

## 免责声明

**风险警告**：加密货币交易存在高风险。本程序仅供学习和研究使用，使用本程序进行实盘交易产生的任何损失由使用者自行承担。

## License

MIT
