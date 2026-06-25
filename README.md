# A股分时异动实时监控 MVP

一个本地可运行的“快速拉升股票雷达”。当前版本内置模拟行情源，用于验证监控逻辑、看板交互和信号评分；后续可以替换为券商、Level-2 或第三方实时行情接口。

## 启动

一键启动新版前后端和 TDX 行情源：

```bash
./start.sh
```

打开：

```text
http://127.0.0.1:5173
```

脚本默认启动：

- TDX 候选池：`http://127.0.0.1:9002/ticks`
- FastAPI 后端：`http://127.0.0.1:8788`
- Vite 前端：`http://127.0.0.1:5173`

可选覆盖：

```bash
START_TDX=0 MARKET_HTTP_URL=http://127.0.0.1:9000/ticks ./start.sh
BACKEND_PORT=8790 FRONTEND_PORT=5174 ./start.sh
```

旧版单体页面：

```bash
python3 server.py
```

打开：

```text
http://127.0.0.1:8787
```

### 多端 API 服务

新的多端入口使用 FastAPI，复用当前策略、候选池、复盘和回测逻辑，同时提供 WebSocket，适合后续接 Web、移动端、桌面端和通知服务。

首次安装依赖：

```bash
python3 -m pip install -r requirements.txt
```

启动 FastAPI 版本：

```bash
DATA_SOURCE=http MARKET_HTTP_URL=http://127.0.0.1:9002/ticks uvicorn backend.app:app --host 127.0.0.1 --port 8788
```

打开：

```text
http://127.0.0.1:8788
```

实时通道：

- `/events`：兼容现有 Web 页面的 SSE 推送
- `/ws/radar`：给移动端、桌面端、通知端使用的 WebSocket 推送

### 通知提醒

后端内置轻量通知中心，默认只记录最近通知；配置 `BARK_URL` 后会推送到标准 Bark，配置 `OMNI_BARK_TOKEN` 后会推送到鸿蒙“全能消息推送Bark”。

```bash
export BARK_URL='https://api.day.app/你的Key'
export OMNI_BARK_TOKEN='全能消息推送Bark里的token'
export OMNI_BARK_CHANNEL_ID=''  # 可选，填了则按频道推送
export OMNI_BARK_SENDER='GuPiao'
export OMNI_BARK_API_BASE='http://www.ggsuper.com.cn/push/api/v1'
export NOTIFY_COOLDOWN_SEC=300
export NOTIFY_SECTOR_PULSE_THRESHOLD=3
```

当前规则：

- A 级异动信号
- 强关注候选
- 关注股异动，前端“我的关注”同步到后端后触发
- 板块共振，默认同一候选板块达到 3 只提醒
- 通知中心面板可直接调整规则开关、冷却时间、板块阈值，并发送测试推送

接口：

- `GET /api/notifications/recent?limit=50`：最近通知和通知配置状态
- `GET /api/notifications/config?sector_pulse_threshold=5`：更新通知策略配置
- `GET /api/notifications/test`：发送 Bark 测试推送
- `GET /api/preferences`：多端共享的关注 / 屏蔽列表
- `GET /api/preferences/add?list=watchlist&code=600000`：加入关注或屏蔽
- `GET /api/preferences/remove?list=watchlist&code=600000`：移出关注或屏蔽

### 新版前端

新版多端前端放在 `frontend/`，使用 React + Vite + TypeScript。开发时默认代理到 `http://127.0.0.1:8788`。

```bash
cd frontend
npm install
npm run dev
```

打开：

```text
http://127.0.0.1:5173
```

如需代理到其它后端：

```bash
VITE_PROXY_TARGET=http://127.0.0.1:8788 npm run dev
```

### 打板工作台

当前项目内置专属“打板”页面：

```text
http://127.0.0.1:5173/limit-up.html
```

能力：

- 收盘后读取东方财富涨停池，生成“明日重点关注”和昨日涨停全池
- 15:00 后自动交给 OpenClaw agent 筛选“核心盯盘 / 观察池 / 风险剔除”，也可在页面手动点击 `OpenClaw筛选`
- 明日重点推送优先发送 OpenClaw 核心票、市场观点和剔除数量
- 第二天实时监控昨天涨停板股票，只在出现强承接、封板确认等买入信号时 Bark 推送
- 买点推送按 OpenClaw 分层区分：核心票强提醒、观察池普通提醒、风险剔除票只做风险观察
- 页面优先展示“今日买点 / 核心盯盘 / 异动未确认”，全量列表默认折叠，可展开查看“明日重点 / 昨日涨停池 / 板块复盘”
- 次日监控会生成 `data/limit_up_next_day_review_latest.json`，用于统计核心票/观察票的开盘、封板和买点表现
- 接口：`GET /api/limit-up/tomorrow-focus?notify=1`、`GET /api/limit-up/openclaw-review?max_items=120&timeout=600&notify=1`、`GET /api/limit-up/next-day-monitor?notify=1`

可选 Postgres 旁路存储：在 `.env.local` 中配置 `DATABASE_URL` 后，打板数据会同步写入 `limit_up_snapshots`、`limit_up_signals`、`limit_up_focus_reports`、`limit_up_focus_stocks`、`limit_up_next_day_monitors` 和 `limit_up_next_day_rows`。未配置时继续使用本地 JSON/JSONL 文件。

```bash
DATABASE_URL=postgresql+psycopg://user:password@localhost:5432/gupiao
```

## 当前能力

- 实时推送候选股票行情
- 识别 1/3/5 分钟快速拉升
- 结合成交额、量能放大、主动买入、盘口强度、板块共振评分
- 按 A/B/C 级显示异动
- 支持阈值配置和风险过滤
- 自动保存异动信号到 `data/signals.jsonl`
- 支持历史接口和 CSV 导出
- 同股信号冷却，避免短时间重复刷屏；等级提升或评分大幅提高会再次提醒
- 前端支持关注池、屏蔽池和只看关注过滤；设置保存在浏览器本地
- 实时跟踪报警后的当前收益、最高收益、最低回撤和跟踪时长
- 统计报警样本的正收益率，并按 A/B/C 等级汇总表现
- 数据源健康监控：行情延迟、批次数、Tick 数、SSE 连接数和运行时长
- 行情源异常自动重试，并展示错误次数、重试次数和最近错误
- A 股交易时段识别：集合竞价、连续竞价、午休、收盘、非交易日
- 支持 `data/trading_calendar.json` 覆盖休市日和特殊交易日；已内置上交所 2026 年部分节假日休市安排
- 页面可调整核心监控阈值，并保存到 `data/monitor_config.json`
- 配置更新带白名单和范围限制，支持一键恢复默认值
- HTTP 行情适配器会跳过坏 tick，并在数据状态里展示坏行数量和最近坏行错误
- 支持 `data/watch_universe.json` 股票池过滤；`include` 为空表示不过滤，`exclude` 用于排除
- 页面可维护股票池：添加/删除关注池和排除池代码
- 板块/题材配置迁移到 `data/sectors.json`，页面可添加/删除板块成分代码
- 盘中复盘报告：汇总板块、等级表现、质量标签、高分信号和运行状态
- 报警后表现跟踪落盘到 `data/tracks.jsonl`

## 接真实行情

实现 `market_data.py` 中的 `MarketDataSource` 协议即可。系统只需要每秒收到一批 `Tick`，不绑定具体数据供应商。

也可以先用通用 HTTP JSON 适配器：

```bash
DATA_SOURCE=http MARKET_HTTP_URL=http://127.0.0.1:9000/ticks python3 server.py
```

本地样例 HTTP 行情源：

```bash
python3 sample_ticks_server.py
```

另开一个终端启动雷达：

```bash
DATA_SOURCE=http MARKET_HTTP_URL=http://127.0.0.1:9000/ticks python3 server.py
```

### TDX 全市场候选池

用于“不升级 TickDB 也能扫全市场”的模式：OpenTDX 负责全市场涨速/成交额候选池，主雷达直接读取候选 tick；TickDB 只订阅 TDX 输出的 Top 5 做精盯。

首次安装 OpenTDX 到本地虚拟环境：

```bash
python3 -m venv .venv-tdx
.venv-tdx/bin/pip install opentdx
```

启动 TDX 候选池：

```bash
.venv-tdx/bin/python tdx_candidate_server.py
```

另开一个终端启动主雷达：

```bash
DATA_SOURCE=http MARKET_HTTP_URL=http://127.0.0.1:9002/ticks MARKET_HTTP_INTERVAL=1 python3 server.py
```

候选池会生成 `data/tdx_top_symbols.json`，供 TickDB 订阅 Top 5：

```bash
export TICKDB_API_KEY='你的 TickDB Key'
export TICKDB_SYMBOLS_FILE='data/tdx_top_symbols.json'
python3 tickdb_ticks_server.py
```

可选配置：

- `TDX_PORT`：候选池端口，默认 `9002`
- `TDX_INTERVAL`：扫描间隔秒数，默认 `3`
- `TDX_SCAN_COUNT`：每轮从 TDX 拉取的涨速/成交额排序数量，默认 `200`
- `TDX_OUTPUT_COUNT`：输出给主雷达的候选数量，默认 `80`
- `TDX_TOP_SYMBOL_COUNT`：写入 TickDB 精盯文件的数量，默认 `5`
- `TDX_TOP_REFRESH_INTERVAL`：Top 精盯名单最短刷新间隔，默认 `30` 秒，避免 TickDB 频繁重连
- `TDX_TOP_MIN_HOLD_SEC`：进入 TickDB 精盯池后的最短持有时间，默认 `180` 秒
- `TDX_TOP_REPLACE_RATIO`：新候选替换老候选所需分数倍率，默认 `1.2`
- `TDX_TOP_ALERT_HOLD_SEC`：已触发报警的股票在精盯池内优先保留时间，默认 `600` 秒
- `TDX_TOP_COOLDOWN_SEC`：被踢出精盯池后的冷却时间，默认 `120` 秒
- `TDX_EXCLUDE_ST`：过滤 ST/退市风险股，默认 `1`
- `TDX_EXCLUDE_NEW`：过滤 N/C/U/W 等新股或特殊上市初期标的，默认 `1`
- `TDX_EXCLUDE_BJ`：过滤北交所，默认 `1`
- `TDX_EXCLUDE_GEM`：过滤创业板，默认 `1`
- `TDX_EXCLUDE_STAR`：过滤科创板，默认 `1`
- `TDX_GEM_MAX_CHANGE_PCT`：创业板候选最大当日涨跌幅阈值，默认 `9.5`
- `TDX_GEM_MAX_RISE_SPEED_PCT`：创业板候选最大瞬时涨速阈值，默认 `3.0`
- `TDX_GEM_MAX_TURNOVER_RATE`：创业板候选最大换手率阈值，默认 `18`
- `TDX_MAIN_MIN_CHANGE_PCT`：主板候选最低当日涨幅，默认 `0.5`
- `TDX_MAIN_MAX_CHANGE_PCT`：主板候选最大当日涨幅，默认 `7.5`
- `TDX_MAIN_MAX_TURNOVER_RATE`：主板候选最大换手率，默认 `16`

### TickDB 实时行情桥

TickDB 走 WebSocket 推送，本项目用 `tickdb_ticks_server.py` 把它转换成本地 HTTP `/ticks`，主雷达仍然用通用 HTTP 适配器读取。

```bash
export TICKDB_API_KEY='你的 TickDB Key'
export TICKDB_SYMBOLS='600000.SH,000001.SZ,600030.SH'
python3 tickdb_ticks_server.py
```

全市场 A 股候选池：

```bash
export TICKDB_API_KEY='你的 TickDB Key'
export TICKDB_SYMBOLS='CN'
python3 tickdb_ticks_server.py
```

全市场模式会优先尝试 TickDB 品种接口；如果该接口无权限，会使用本地缓存 `data/tickdb_cn_symbols.json`。当前实时推送仍受 TickDB WebSocket 订阅上限约束，若返回 `Subscription limit exceeded`，需要升级套餐或设置 `TICKDB_MAX_SYMBOLS` 分批测试。

如果套餐订阅数有限，可以先限制数量：

```bash
export TICKDB_SYMBOLS='CN'
export TICKDB_MAX_SYMBOLS=300
python3 tickdb_ticks_server.py
```

另开一个终端启动雷达：

```bash
DATA_SOURCE=http MARKET_HTTP_URL=http://127.0.0.1:9001/ticks python3 server.py
```

可选配置：

- `TICKDB_PORT`：本地行情桥端口，默认 `9001`
- `TICKDB_SYMBOLS`：订阅代码，逗号分隔；设为 `CN` 时自动拉取 TickDB A 股全市场代码池；不设置时优先读取 `data/watch_universe.json` 的 `include`
- `TICKDB_MAX_SYMBOLS`：全市场模式下限制订阅数量，默认 `0` 表示不限制
- `TICKDB_SUBSCRIBE_CHUNK`：WebSocket 分批订阅大小，默认 `200`
- `TICKDB_WS_URL`：TickDB WebSocket 地址，默认 `wss://api.tickdb.ai/v1/realtime`
- `TICKDB_SYMBOLS_CACHE`：A 股代码池缓存，默认 `data/tickdb_cn_symbols.json`

健康检查：

```bash
curl http://127.0.0.1:9001/health
```

接口返回格式：

```json
{
  "ticks": [
    {
      "code": "600030",
      "name": "中信证券",
      "sector": "券商",
      "board": "main",
      "price": 23.4,
      "prev_close": 22.8,
      "volume": 120000,
      "turnover": 2808000,
      "active_buy_ratio": 0.61,
      "bid_amount": 12000000,
      "ask_amount": 8000000
    }
  ]
}
```

## API

- `GET /api/snapshot`：当前异动快照
- `GET /api/signals/history?limit=200`：最近信号
- `GET /api/signals/export`：导出 CSV
- `GET /api/tracks/export`：导出报警后表现跟踪 CSV
- `GET /api/focus/next-day?limit=100`：强关注样本的次日表现跟踪
- `GET /api/focus/next-day?limit=100&include_shadow=1`：包含影子策略样本的次日表现跟踪
- `GET /api/focus/strategy?days=30`：强关注策略每日评分和趋势
- `GET /api/focus/advice?limit=300`：基于盘中回放和次日复盘生成自动调参建议
- `GET /api/backtest/focus?entry=trigger&exit=m5`：基于系统已记录的强关注样本做实盘样本验证
- `GET /api/backtest/history-rapid?date=2026-05-22&max_symbols=50`：历史 1 分钟回放接口，已降为高级工具，不作为主流程依赖
- `GET /api/focus/next-day/export`：导出强关注次日表现 CSV
- `GET /api/health/full`：主服务、TDX、TickDB、候选池、交易日历、次日样本的全链路健康检查
- `GET /api/report`：盘中复盘报告
- `WS /ws/radar`：多端实时推送，连接后先返回 `snapshot`，随后推送 `market`

## 页面结构

- `/`：实时雷达，保留重点盯盘、候选观察、实时异动、板块、次日跟踪简版
- `/review.html`：复盘分析，包含强关注次日表现、盘中统计、最近信号
- `/backtest.html`：样本验证，基于强关注样本比较不同买卖方式
- `/settings.html`：配置，包含股票池、板块配置、监控阈值
- `/diagnostics.html`：运行诊断，包含主服务状态、上游状态、候选池过滤统计

## 强关注次日跟踪

系统会在候选池接口刷新时识别 `强关注` 股票，并写入 `data/focus_next_day.json`。第二个交易日这些股票再次出现在行情 tick 中时，会自动更新：

- 次日开盘涨跌 `gap_pct`
- 次日当前涨跌 `next_return_pct`
- 次日最高 / 最低涨跌 `next_high_return_pct` / `next_low_return_pct`
- 次日振幅回撤 `next_drawdown_pct`
- 次日冲高回落幅度 `next_giveback_pct`
- 复盘评分 `review_score`
- 结果分型 `review_label`：如 `强兑现`、`冲高回落`、`小幅兑现`、`低开走弱`、`未兑现`
- 当前状态：`等待次日` 或 `次日跟踪中`
- 预期次交易日 `expected_next_trading_date`

`/review.html` 会把强关注样本展示为逐股复盘表，便于观察高开、冲高、收盘收益、回落和兑现质量。也可以通过 `/api/focus/next-day/export` 导出 CSV 做复盘。

次日判断使用 `data/trading_calendar.json`，会跳过周末、休市日，并支持特殊交易日。

### 强关注盘中回放

强关注首次触发后，系统会固定触发价和触发时间，并持续记录触发后的盘中表现：

- 当前收益 `intraday_current_return_pct`
- 触发后最高 / 最低收益 `intraday_max_return_pct` / `intraday_min_return_pct`
- 1 / 3 / 5 / 10 分钟收益 `intraday_m1_return_pct` / `intraday_m3_return_pct` / `intraday_m5_return_pct` / `intraday_m10_return_pct`
- 盘中评分 `intraday_score`
- 盘中结果分型 `intraday_label`：如 `持续走强`、`冲高回落`、`小幅延续`、`触发回撤`、`未延续`

`/review.html` 的“强关注触发后表现”表用于判断强关注是否适合追入、等待回踩，还是更适合盘中快进快出。

### 自动调参建议

`/api/focus/advice` 会汇总强关注样本的盘中延续率、冲高回落比例、弱延续比例、次日上涨率、结果分型和优势板块，并生成结构化建议：

- 样本不足：继续积累，不直接改主策略
- 弱延续高：提高主动买入、成交额或板块热度门槛
- 冲高回落高：增加盘中止盈或降级观察规则
- 次日弱：降低隔夜权重，优先做盘中
- 影子策略明显更好：继续观察是否具备升级条件

这些建议会显示在 `/review.html` 的“自动调参”区域，作为策略调整参考。

### 实盘样本验证

`/backtest.html` 基于已记录的强关注样本做验证，不需要额外历史行情。当前支持：

- 买入方式：触发价、次日开盘
- 卖出方式：1 / 3 / 5 / 10 分钟、盘中当前、盘中最高、次日开盘、次日最高、次日收盘
- 过滤条件：最低信号分、最低盘中分、最低复盘分、是否包含影子策略
- 输出：样本数、胜率、平均收益、最好/最差单笔、盈亏比、按板块和策略版本分组表现

这是实盘留痕验证，不是完整历史分时回放。系统主线改为实时触发、当日留痕、次日观察，避免依赖不稳定的外部历史分钟线。

### 历史快速拉升回放

`/backtest.html` 的高级区域仍保留“历史快速拉升回放”，但该功能依赖东方财富近期 1 分钟 K 线，当前不作为主流程使用：

- 触发条件：1 分钟涨速、3 分钟涨速、2 分钟成交额、最高日涨幅
- 可选条件：当日触及涨停，适合筛选“快速拉升后封板”的样本
- 后续收益：1 / 3 / 5 / 10 分钟收益、10 分钟最高/最低、次日开盘/最高/最低/收盘
- 扫描范围：可填指定股票，也可从本地 A 股代码池抽取前 N 只
- 默认排除：北交所、创业板、科创板；可在页面勾选放开创业板/科创板
- 勾选“当日涨停”且不填指定股票时，会优先读取东方财富涨停池，再对涨停股验证快速拉升和次日走势，避免逐只扫描全市场
- “多日期”支持逗号或换行输入，例如 `20260521,20260522`，系统会逐日扫描并输出按日期汇总
- 历史分钟线和涨停池会缓存到 `data/history_cache`；首次扫描较慢，重复运行同一日期/股票会直接走本地缓存
- 页面回放会提交后台任务并显示进度；接口为 `/api/backtest/history-rapid/start` 和 `/api/backtest/history-rapid/job?id=...`

注意：东方财富 1 分钟历史通常只覆盖近期交易日，且部分日期会缺失分钟线。没有稳定历史分钟数据源时，不建议把它作为策略判断依据。

### 策略版本

- `focus-v1`：当前实时雷达主策略，参与盘中强关注显示和主策略次日跟踪。
- `focus-v2-shadow`：影子策略，只在复盘统计中记录和对比，不影响实时雷达的强关注排序。
