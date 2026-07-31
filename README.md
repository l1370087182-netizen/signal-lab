# SIGNAL LAB · 美股投研系统

只读美股投研工具：搜索与自选、技术信号与支撑阻力、板块与恐惧指数、AI 资讯/财报分析。  
**不含下单、不含实盘交易；所有结论仅供研究参考，不构成投资建议。**

## 功能概览

| 模块 | 说明 |
|------|------|
| 搜索 / 自选 | 按代码或名称搜索美股；自选与分组本地 SQLite 持久化 |
| 股票详情 | 报价摘要、核心技术指标（无 K 线） |
| 详细分析 | 全量指标、买卖强度、交易计划（入场 / 止损 / 止盈与风险收益比） |
| 买卖筛选 | 按买入/卖出、强烈/谨慎筛选，按强度排名分页 |
| 支撑阻力 | 强弱支撑/阻力位，用于交易计划与风险归一化展示 |
| 板块 / 恐惧指数 | 板块详情与市场情绪参考 |
| AI 分析 | 抓取资讯 → 分块检索 → 大模型综述（进度日志 + 完整结果） |
| 财报分析 | 结构化财报与分析师预期辅助，含下季度财报预测段落 |
| 历史分析 | 本地保存最近各 10 条一般分析 / 财报分析，可回看 |

## 技术栈

- **前端**：Vite + React + TypeScript
- **后端**：FastAPI + pandas + ta
- **行情**：Yahoo Finance 日线（网络可达时优先）+ 东方财富等补充 / akshare 兜底
- **舆情**：财经资讯关键词情绪（原文提取后不落盘）
- **AI**：兼容 OpenAI Chat Completions 的任意模型（默认示例为 Moonshot / Kimi）
- **存储**：SQLite（自选、AI 历史）

## 快速开始

### 环境要求

- Python 3.10+
- Node.js 18+
- （可选）可访问 Yahoo Finance / 模型 API 的网络环境

### 1. 后端

```powershell
cd backend
python -m pip install -r requirements.txt
copy .env.example .env
# 编辑 .env，填入 MODEL_API_KEY 等（见下文「AI 配置」）
python -m uvicorn main:app --reload --host 127.0.0.1 --port 9000
```

健康检查：<http://127.0.0.1:9000/api/health>

> 若本机 8000 被系统保留端口占用，请继续使用 **9000**。

### 2. 前端

另开终端：

```powershell
cd frontend
npm install
npm run dev
```

浏览器打开：<http://127.0.0.1:5173>  
开发模式下前端将 `/api` 代理到后端 `9000` 端口。

### 控制窗口（可选）

双击仓库根目录 `control.bat`，可一键启动/停止后端与前端、打开浏览器、查看日志。  
若默认端口被占用，会自动改用其它空闲端口。可选开启 Cloudflare 临时公网（需已安装 cloudflared）。

## AI 配置

复制 `backend/.env.example` 为 `backend/.env`（该文件已被 `.gitignore` 忽略，**请勿提交密钥**）：

```env
MODEL_NAME=kimi-k3
MODEL_API_KEY=your-api-key-here
MODEL_BASE_URL=https://api.moonshot.cn/v1
# 可选；部分模型仅允许 1
MODEL_TEMPERATURE=1
```

- 任意提供 OpenAI 兼容 `/v1/chat/completions` 的服务均可：改 `MODEL_NAME` / `MODEL_API_KEY` / `MODEL_BASE_URL` 后重启后端即可。
- 未配置有效密钥时，行情与规则信号仍可用；AI / 财报分析会失败并提示配置问题。

## 主要 API

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| GET | `/api/search?q=` | 搜索股票 |
| GET | `/api/quote/{symbol}` | 报价 |
| GET | `/api/indicators/{symbol}?level=summary\|full` | 技术指标 |
| GET | `/api/analysis/{symbol}` | 全指标 + 买卖建议 / 交易计划 |
| GET/POST/DELETE | `/api/watchlist` | 自选增删查 |
| GET | `/api/screener` | 买卖信号筛选 |
| POST | `/api/ai-analysis` 等 | AI / 财报分析（含进度事件） |
| GET | `/api/ai-history` | AI 分析历史列表 |
| GET | `/api/ai-history/{id}` | 单条历史详情 |

完整路由以 `backend/main.py` 为准。

## 目录结构

```
├── backend/           # FastAPI 服务
│   ├── main.py
│   ├── data/
│   ├── indicators/
│   ├── db/
│   ├── .env.example
│   └── requirements.txt
├── frontend/          # Vite React 前端
└── README.md
```

## 安全与隐私

- 不要将 `backend/.env`、数据库文件或任何 Token 提交到 Git。
- 本仓库默认忽略 `.env`、`*.db`、缓存与依赖目录。
- 若曾在聊天或截图中泄露过 GitHub / 模型 API Token，请立刻在对应平台**撤销并重新生成**。

## 免责声明

本项目输出的技术信号、交易计划与 AI 文本均为研究与教育用途，**不构成任何投资、交易或财务建议**。市场有风险，决策自负。

## License

未另行声明时，仅供个人学习与研究使用。若需开源协议或商用，请自行补充 LICENSE。
