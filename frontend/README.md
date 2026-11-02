# 足球分析工作台

数据与模型的功能边界、赛事隔离和操作流程见 [执行约束](../docs/football-system-contract.md)。

Vue 3 + TDesign Vue Next。业务界面只有真实数据模式；浏览器不内置比赛、赔率、阵容或预测样例。

界面文案保持简短：不放英文眉题、宣传标语、重复说明和实现细节。保留必要的操作标签、数据时间、缺失状态与错误提示；产品和架构说明留在文档中。

## 启动

在仓库根目录执行：

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-console.txt
.\.venv\Scripts\python.exe dashboard.py --port 8891 --console-only --cache-interval 0
```

打开 `http://127.0.0.1:8891/club-preview.html` 或 `/console`。不要再使用 `python -m http.server` 运行产品，它没有数据接口。`--console-only` 关闭旧世界杯后台任务，根地址也进入新工作台。已有构建产物纳入版本控制，无需安装 Node 即可运行。

## 前端开发

```powershell
cd frontend
npm ci
npm run dev
npm run build
```

开发时 Vite 将 `/api` 代理到 8891。构建输出 `web/console`，同步更新 `web/club-preview.html`；发布脚本清理不再被引用的旧 JS/CSS。

## 接口

| 功能                         | 接口                                                                 |
| ---------------------------- | -------------------------------------------------------------------- |
| 已采集场次、日期、时效状态   | `GET /api/v1/matches`                                                |
| 主动同步官方场次             | `POST /api/v1/sync`                                                  |
| 单场实时详情、来源、赔率历史 | `GET /api/v1/matches/{provider:id}`                                  |
| 预测并归档                   | `POST /api/v1/matches/{provider:id}/predict`；可选 `narrative: true` |
| 关注、笔记与历史版本         | `GET /api/v1/state`                                                  |
| 修改关注或笔记               | `POST /api/v1/state`；`kind / key / value`                           |
| 复盘列表、更新赛果           | `GET /api/v1/reviews`、`POST /api/v1/reviews/sync`                   |
| 数据源状态与计算版本         | `GET /api/v1/capabilities`                                           |

赔率状态来自各玩法，不能用比赛级单关标记代替。ESPN 对应关系需要赛事、双方球队、开赛时间共同匹配。缺失数据不会用本地旧世界杯情报或样例补齐。

## 存储与配置

- 数据库：`data/console/football.sqlite3`，包含真实比赛、快照、预测版本、关注和笔记，已加入 Git 忽略。运行时采用 WAL；备份建议停止服务后复制数据库及关联 WAL 文件，或使用 SQLite 在线备份 API。
- `FOOTBALL_DB_PATH`：覆盖数据库位置，供测试和部署使用。
- `FOOTBALL_DIRECT_NETWORK=1`（默认）：数据适配器直接连接来源；设为 `0` 使用环境代理。不修改系统代理配置。
- 工作台不调用大模型。历史解读只读保留，本地数值预测不依赖大模型密钥。
- 当前运行在本机工作空间，不含对外账户鉴权。数据库结构保留 workspace 隔离字段，外网部署前必须实现服务端身份认证。

## 预测能力边界

已实现版本化市场基线、五种玩法观察及逐玩法复盘。近期表现仅作事实对照，不用固定权重修改市场概率。足球进球模型支持分联赛时间回测、模型保存及独立前瞻观察；数据不齐时保持待训练。已开赛、取消、官方状态不可验证或关键赔率不完整时，不创建新的赛前版本。

伤停、xG、授权外盘、多赛季训练历史仍需接入。后台训练任务通过 `POST /api/v1/training/evaluate` 启动，由 capabilities 的 `training.job` 返回进度。数据格式、训练条件及模型限制见 [联赛数据与模型](../docs/league-data-model.md)。

## 验证

```powershell
# 仓库根目录
.\.venv\Scripts\python.exe -m unittest tests.test_console_domain tests.test_console_routes
cd frontend
npm test
npm run build
npm run test:e2e
```

浏览器测试采用系统 Chrome、8893 临时静态服务和测试专用 API fixture。生产包不导入 fixture。真实来源另进行联网验收，不把 mock 测试当作数据接入成功。
