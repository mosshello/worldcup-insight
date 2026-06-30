# worldcup-insight 项目功能进度文档

> 更新日期：2026-06-29  
> 当前分支：`dev/jun-odds`（基于 `master` 的 `c9b5bc9 init project` 扩展，**尚未 commit / push**）  
> 协作说明：个人功能在 `dev/jun-odds` 开发，`master` 保持不动供另一人并行修改

---

## 1. 项目定位

**世界杯盘口洞察工具**：以**中国体育彩票竞彩网**固定奖金为主盘，结合**外网博彩公司**欧赔/亚盘走势作辅盘，输出胜平负方向、比分参考、盘口变动分析与可视化展示。

**技术约束**：核心逻辑仅依赖 **Python 3 标准库**；前端 Chart.js 通过 CDN 加载，无需 npm。

**重要声明**：所有输出均为市场定价推演，不构成投注建议或赛果保证；90 分钟常规时间口径，不含加时/点球。

---

## 2. 整体架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        入口层 (CLI / Web)                        │
│  main.py │ predict.py │ watch_odds.py │ dashboard.py │ scripts/ │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                     worldcup_mvp 核心包                          │
│  analyzer          欧赔去水、信心、规则分析（MVP 原始能力）        │
│  sporttery_api     体彩官方 API 客户端                           │
│  sporttery_cache   API 失败时的本地快照缓存                       │
│  score_predictor   未开赛赛事比分 + 方向预测                      │
│  fusion_predictor  体彩主盘 + 外网辅盘融合方向                    │
│  movement_analyzer 连续快照盘口变动分析                           │
│  collector         多数据源采集抽象                               │
│  fox_scraper       FOX Sports 爬虫（外网辅盘）                    │
│  the_odds_api      The Odds API 客户端（外网辅盘）                │
│  dashboard_data    仪表盘 API 数据聚合                            │
└────────────────────────────┬────────────────────────────────────┘
                             │
┌────────────────────────────▼────────────────────────────────────┐
│                         数据源                                    │
│  体彩 getMatchCalculatorV1  当前可售赛事 + 固定奖金               │
│  体彩 getFixedBonusV1       单场历史走势 + 猜比分(crs)            │
│  FOX Sports 页面            FanDuel Moneyline                    │
│  The Odds API               h2h + spreads（需 Key）               │
│  本地 JSON / feed 文件      手动或自建爬虫对接                     │
└─────────────────────────────────────────────────────────────────┘
```

---

## 3. 已完成功能清单

### 3.1 基础分析（master 原有 · 已完成）

| 功能 | 入口 | 状态 |
|------|------|------|
| 本地 JSON 欧赔去水分析 | `python main.py` | ✅ 稳定 |
| 胜平负概率 / 排序 / 信心 | `analyzer.py` | ✅ |
| 淘汰赛 90 分钟口径说明 | `analyzer.py` | ✅ |
| JSON 机器可读输出 | `main.py --json` | ✅ |
| 单元测试 | `tests/test_analyzer.py` | ✅ 4 项 |

### 3.2 盘口变动追踪（dev/jun-odds · 已完成）

| 功能 | 入口 | 状态 |
|------|------|------|
| 欧赔 + 亚盘快照存储 | `odds_snapshot.py` | ✅ |
| 连续快照变动分析 | `movement_analyzer.py` | ✅ |
| 欧赔与亚盘交叉信号 | `movement_analyzer.py` | ✅ |
| 多数据源采集器 | `collector.py` | ✅ |
| 持续采集 CLI | `watch_odds.py watch` | ✅ |
| 示例历史数据 | `data/odds_history_bra-jpn.json` | ✅ |
| 单元测试 | `tests/test_movement.py` 等 | ✅ 10 项 |

### 3.3 外网数据源（dev/jun-odds · 已完成）

| 数据源 | 模块 | 欧赔 | 亚盘 | 需 Key | 状态 |
|--------|------|------|------|--------|------|
| FOX Sports 爬虫 | `fox_scraper.py` | ✅ | ❌ | 否 | ✅ 可用 |
| The Odds API | `the_odds_api.py` | ✅ | ✅ | 是 | ✅ 已实现 |
| 本地 feed 文件 | `collector.py` | ✅ | ✅ | 否 | ✅ |

### 3.4 体彩主盘（dev/jun-odds · 已完成）

| 功能 | 说明 | 状态 |
|------|------|------|
| 当前可售赛事拉取 | `getMatchCalculatorV1.qry` | ✅ |
| 单场固定奖金历史 | `getFixedBonusV1.qry` | ✅ |
| 胜平负 / 让球胜平负解析 | `had` / `hhad` | ✅ |
| 猜比分(crs) 最低项 → 比分预测 | `score_predictor.py` | ✅ |
| **未开赛**赛事自动筛选 | `fetch_upcoming_matches()` | ✅ |
| 融合方向预测 | `fusion_predictor.py` | ✅ |
| 中英文队名映射 | `data/team_name_map.json` | ✅ 部分世界杯队 |
| API 缓存兜底 | `sporttery_cache.py` | ✅ |
| 单元测试 | `tests/test_sporttery.py` 等 | ✅ |

### 3.5 预测 CLI（dev/jun-odds · 已完成）

| 命令 | 说明 |
|------|------|
| `python predict.py list` | 列出体彩未开赛赛事 |
| `python predict.py predict --home X --away Y` | 单场融合预测 |
| `python scripts/today_score_predict.py list` | 同上（动态拉取） |
| `python scripts/today_score_predict.py predict` | 全部未开赛比分预测 |

### 3.6 可视化仪表盘（dev/jun-odds · 基本完成）

| 功能 | 状态 | 备注 |
|------|------|------|
| Web 服务 | ✅ | `python dashboard.py` → `:8765` |
| 未开赛赛事卡片 | ✅ | 来自体彩 API |
| 比分 / 方向 / 信心展示 | ✅ | |
| 体彩胜平负概率环形图 | ✅ | Chart.js |
| 体彩固定奖金走势折线图 | ✅ | had / hhad |
| 融合分析 + 外网对比 | ✅ | |
| 刷新按钮 | ✅ | |
| 本地缓存展示 | ✅ | API 失败时读 `data/cache/` |
| 自动刷新 | ❌ | 未做 |
| 连接中断容错 | ⚠️ | 见「已知问题」 |

---

## 4. 模块与文件对照

```
worldcup-insight/
├── main.py                 # 原始 MVP：本地 JSON 分析
├── predict.py              # 体彩融合方向预测
├── watch_odds.py           # 盘口采集 + 变动分析
├── dashboard.py            # Web 仪表盘 HTTP 服务
├── scripts/
│   └── today_score_predict.py   # 未开赛比分批量预测
├── web/
│   ├── index.html          # 仪表盘页面
│   ├── app.js              # 前端逻辑
│   └── styles.css          # 样式
├── worldcup_mvp/
│   ├── analyzer.py         # 欧赔去水分析
│   ├── sporttery_api.py    # 体彩 API
│   ├── sporttery_cache.py  # 本地缓存
│   ├── score_predictor.py  # 比分预测
│   ├── fusion_predictor.py # 方向融合
│   ├── movement_analyzer.py
│   ├── odds_snapshot.py
│   ├── collector.py
│   ├── fox_scraper.py
│   ├── the_odds_api.py
│   ├── dashboard_data.py
│   ├── team_names.py
│   └── env_config.py
├── data/
│   ├── matches_2026-06-29.json      # 静态示例（main.py 默认）
│   ├── odds_history_bra-jpn.json    # 外网采集示例历史
│   ├── team_name_map.json
│   └── cache/sporttery_snapshot.json # 体彩 API 成功时的缓存
└── tests/                  # 30 个单元测试，全部通过
```

---

## 5. 数据流说明

### 5.1 主盘（体彩）

1. `getMatchCalculatorV1` → 当前可售、未开赛赛事 + 最新 had/hhad
2. `getFixedBonusV1` → 单场 had/hhad/crs 历史序列
3. 方向：had 去水概率 → 主胜/平/客胜 + 信心
4. 比分：crs 固定奖金最低项 → 最可能比分 + 备选

### 5.2 辅盘（外网）

1. FOX：页面 Moneyline → 美式转十进制欧赔
2. The Odds API：h2h + spreads（可选，`.env` 配置 `ODDS_API_KEY`）
3. 与体彩对比：首选是否一致、概率差值（pp）

### 5.3 预测逻辑（当前版本）

- **不是 ML 模型**，纯赔率市场定价推导
- 方向 = 体彩 had 去水最高项
- 比分 = 体彩 crs 最低固定奖金项
- 信心 = 第一与第二概率差 + 外网是否一致
- 特殊提示：方向≠平局但 crs 最低为平局比分时，输出防平提示

---

## 6. 测试覆盖

```powershell
python -m unittest discover -s tests -v
```

| 测试文件 | 数量 | 覆盖范围 |
|----------|------|----------|
| `test_analyzer.py` | 4 | 欧赔去水、排序、淘汰赛说明 |
| `test_movement.py` | 6 | 快照、变动、采集器 |
| `test_sources.py` | 6 | FOX、The Odds API |
| `test_sporttery.py` | 4 | 体彩解析、融合预测 |
| `test_score_predictor.py` | 5 | 未开赛筛选、crs 解析 |
| `test_dashboard.py` | 3 | 仪表盘数据聚合 |
| **合计** | **30** | **全部通过** |

---

## 7. 已知问题与风险

### 7.1 体彩 API WAF 拦截（高优先级）

- **现象**：`getMatchCalculatorV1` 间歇性返回 HTTP 403（WAF 拦截页）
- **影响**：CLI / 仪表盘无法拉取最新赛事，显示「API 暂不可用」
- **现有缓解**：浏览器 UA 请求头 + `data/cache/sporttery_snapshot.json` 缓存兜底
- **仍不足**：缓存过期后、且 API 持续 403 时，界面仍可能空白

### 7.2 仪表盘连接中断（中优先级）

- **现象**：终端出现 `ConnectionAbortedError: WinError 10053`（浏览器刷新/取消请求时）
- **影响**：偶发 500 日志，不影响服务整体运行
- **待做**：`_send_json` 捕获客户端断开，避免 traceback

### 7.3 仪表盘需重启才能加载新代码（中优先级）

- **现象**：旧进程仍跑旧版 `/api/overview`（返回本地 `matches` 而非体彩数据）
- **解决**：改代码后必须 **Ctrl+C 重启** `python dashboard.py`

### 7.4 外网数据覆盖不全（低优先级）

- FOX 仅覆盖其页面 listed 的场次；无 FOX 数据时辅盘回退为体彩自身
- The Odds API 世界杯赛季可能无赛事，需换 `sport_key` 或等赛季开赛

### 7.5 队名映射不完整（低优先级）

- `team_name_map.json` 仅覆盖部分世界杯球队
- 新联赛 / 新队需手动补充

### 7.6 Git 状态（协作）

- 全部新功能在 `dev/jun-odds`，**尚未 commit**
- 搭档可继续在 `master` 工作，后续需 merge / PR

---

## 8. 配置与环境

| 文件 | 用途 |
|------|------|
| `.env.example` | `ODDS_API_KEY`、`ODDS_SPORT`、`ODDS_REGIONS` 模板 |
| `.gitignore` | 忽略 `.env`、`__pycache__`、`data/cache/`、`odds_live_feed.json` |

---

## 9. 常用命令速查

```powershell
cd d:\worldcup_inisght\worldcup-insight
git checkout dev/jun-odds

# 基础分析（静态 JSON）
python main.py

# 体彩未开赛列表 + 比分预测
python scripts/today_score_predict.py list
python scripts/today_score_predict.py predict

# 融合方向预测
python predict.py predict --home 法国 --away 瑞典

# 盘口采集
python watch_odds.py watch --source sporttery --interval 300

# 可视化（改代码后需重启）
python dashboard.py
# → http://127.0.0.1:8765

# 测试
python -m unittest discover -s tests -v
```

---

## 10. 建议的下一步优化（按优先级）

### P0 · 稳定性（建议先做）

- [x] 体彩 API 请求重试 + 指数退避（403 时自动重试 2–3 次）
- [x] 仪表盘 `ConnectionAbortedError` 静默处理
- [x] 后台定时刷新缓存（dashboard 启动时/async 线程每 N 分钟拉一次体彩）
- [x] 将 `dev/jun-odds` 整理 commit 并 push，方便双人协作

### P1 · 产品体验

- [x] 仪表盘自动刷新（30s / 60s 可配置）
- [x] 比赛卡片显示「距开赛 X 小时」
- [x] 预测结果导出 JSON / CSV
- [x] 单场详情页 URL（`/match/{id}`）便于分享
- [x] 移动端布局优化
- [x] 增加类似参与投注游戏假设金额的盈亏计算（参考 SportteryAPI）
- [x] 每日进行结算可以通过体彩官网获取比赛结束结算信息（预测投注金额 vs 实际赛果，计算盈亏）

### P1.5 · 三源联动（master 合并后）

> 目标：在保留 dev/jun-odds 体彩主盘 + 可视化能力的前提下，接入成员 `UnifiedDataManager`（FIFA + Polymarket + 体彩）三源管线。

- [x] 新增 `unified_bridge.py` 桥接三源索引与仪表盘
- [x] 外网辅盘默认 `auto`：优先 Polymarket，失败回退 FOX / Odds API
- [x] 仪表盘页头展示三源 `doctor` 健康状态（FIFA / Polymarket / 体彩）
- [x] 预测记录写入 `provider_ids`（FIFA / 体彩 / Polymarket 对照）
- [x] 融合分析接入 FIFA 上下文综合信号（`context_pick` / `context_edge`）
- [x] 外网 vs 体彩概率差值 ≥5pp 高亮告警
- [x] 结算复盘同时拉 FIFA 90 分钟比分 vs 体彩 `matchResultList`
- [x] 统一 `sporttery_api` 与 `HttpJsonClient` HTTP 层（403 退避共享）
- [x] 仪表盘「三源交叉模式」：`/api/overview?mode=unified` 仅展示三源均匹配场次

### P2 · 数据与分析增强

- [ ] 体彩 `ttg`（总进球）、`hafu`（半全场）纳入分析
- [ ] 凯利指数 / 返还率 / 价值偏差（参考 SportteryAPI 思路）
- [x] 外网与体彩概率差值告警（超过阈值高亮）—— 首版 5pp 阈值已在仪表盘实现
- [ ] 完场后自动复盘：预测 vs 实际赛果（结合 FIFA 比分 + 结算模块）
- [ ] 扩充 `team_name_map.json` 或自动从 FIFA/体彩代码别名同步

### P3 · 架构与工程

- [ ] 统一 `predict.py` 与 `today_score_predict.py`（避免重复入口）
- [ ] README 路径更新为当前仓库地址
- [ ] 考虑轻量依赖（如 `httpx`）替代 urllib，改善 WAF 兼容性
- [ ] Docker 一键启动 dashboard + 定时采集
- [ ] CI：push 时自动跑 30 个测试

### P4 · 模型化（长期）

- [ ] 引入历史赛果训练简单基准模型（Elo / Poisson）
- [ ] 市场定价 + 统计特征融合
- [ ] 当前阶段**不建议**急于上 ML，先把数据管道做稳

---

## 11. 与 worldcup2026 项目的关系

| 项目 | 数据类型 | 可联动方式 |
|------|----------|------------|
| `worldcup2026` | 赛程、比分、Varzesh3 实时比分 | 可提供 match_id / 队名对照 |
| `worldcup-insight` | 体彩固定奖金、外网欧赔 | **已部分打通**：`provider_ids.fifa_match` 可对照 |

潜在联动：用 worldcup2026 的赛程驱动 `worldcup-insight` 该分析哪场；赛果回填用于复盘（P1.5 待做 FIFA 比分合并结算）。

---

## 12. 当前完成度评估

| 维度 | 完成度 | 说明 |
|------|--------|------|
| 核心分析逻辑 | **92%** | 去水、变动、融合 + FIFA 上下文信号已接入 |
| 体彩数据接入 | **78%** | 双实现并存，桥接层已索引三源 |
| 外网辅盘 | **80%** | Polymarket 优先，FOX/Odds API 回退 |
| 三源联动 | **85%** | P1.5 已完成：桥接、doctor、FIFA 复盘、统一 HTTP、交叉模式 |
| CLI 工具链 | **85%** | 入口略多，待整合 |
| 可视化 | **78%** | 三源状态、差值高亮、模式切换、FIFA 结算列 |
| 测试 | **80%** | 69+ 项单元测试，缺集成/E2E |
| 文档 | **70%** | README + PROGRESS 已更新联动规划 |
| 生产就绪 | **42%** | 个人开发/演示可用，未做部署与监控 |

---

## 13. 修订记录

| 日期 | 说明 |
|------|------|
| 2026-06-30 | P1.5 三源联动完成：unified_bridge、FIFA 复盘、HTTP 统一、交叉模式 |
| 2026-06-30 | 合并 master 三源接口；新增 P1.5 联动规划并实现 unified_bridge |
| 2026-06-29 | 初版：汇总 dev/jun-odds 分支全部已完成功能、已知问题与优化路线图 |

---

**维护建议**：每完成一个优化阶段，更新本文档第 3、7、10、12 节，并在 Git commit message 中引用变更项。
