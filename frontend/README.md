# 足球分析工作台

Vue 3 + TDesign Vue Next。独立前端；保留原有 Python 后端和旧版首页。

## 运行

```powershell
cd frontend
npm ci
npm run build
cd ..
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-console.txt
.\.venv\Scripts\python.exe dashboard.py --port 8892 --cache-interval 0
```

打开 `http://127.0.0.1:8892/console`。开发时运行 `npm run dev`，Vite 将 `/api` 转发到 8892。

现有静态预览仍可打开 `http://127.0.0.1:8891/club-preview.html`；静态服务器没有 `/api`，因此用于示例模式。页面右上角可切换模式，真实接口失败时显示错误，不偷偷回退示例。

构建生成 `web/console/`，并更新原 `web/club-preview.html` 为同一应用入口。构建产物纳入版本控制，使用 Python 服务无需安装 Node。

## 第一版范围

- 比赛中心：销售日、赛事、玩法、搜索、仅在售、仅单关、仅关注、分页、CSV 导出。
- 单场详情：基本面、预计阵容/伤停、体彩 SP 与外盘对照、赔率历史、AI 分析、分析版本、笔记。
- 复盘：赛前/赛后对照、筛选、复盘笔记、CSV 导出。
- 数据与模型：数据覆盖说明、训练配置创建/校验/删除、模型版本与对比示例。

所有演示数据均显式标注，非真实比赛预测。阵容图使用位置而非虚构真实球员伤停。

## 真实接口与边界

| 功能                     | 接口 / 状态                                                         |
| ------------------------ | ------------------------------------------------------------------- |
| 体彩比赛和已接入 SP      | `GET /api/sporttery/matches`                                        |
| 分析及版本保存           | `GET /api/sporttery/predict/{match_id}`；市场定价分析，非俱乐部模型 |
| 既有复盘读取             | `GET /api/analyze/reviews`                                          |
| 现有能力读取             | `GET /api/console/capabilities`                                     |
| 俱乐部赛果统计、阵容、xG | 等待数据接入，真实模式展示缺失状态                                  |
| 俱乐部训练执行、启用模型 | 等待服务接入；第一版仅保存配置，无伪造进度                          |

关注、笔记、分析快照和训练配置只存储在当前浏览器 localStorage，示例和真实分别隔离。尚无账号、权限、服务端同步；不能作为付费平台直接对外部署。

## 统一规范

TDesign 色值与组件：品牌蓝 `#0052D9`，灰白背景；所有圆角统一覆盖为 0。正文 14px、辅助 12px、区块标题 16px、页面标题 24px。桌面控件 32px；8px 基础间距；表格固定表头字号及操作列。窄屏改为单列，表格自身滚动；键盘焦点与减少动效设置保留。

## 验证

```powershell
npm test
npm run build
npx playwright test
```

浏览器测试使用系统 Chrome，在 8893 自动启动静态服务。覆盖筛选、关注、四个页签、版本生成、笔记持久化、复盘导出、训练配置校验、API 错误隔离和窄屏溢出检查。截图位于 `frontend/test-results/screens/`。
