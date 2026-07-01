# 亮色扁平化 Pages 界面技术规格书

## 目标
- 将当前深色渐变控制台改为明亮、扁平、低阴影的浅色界面，不改变模型、投注组合与账本数据。

## 影响范围
| 文件 | 原因 |
|---|---|
| `scripts/generate_pages.py` | 生成并引用亮色扁平主题样式 |
| `docs/flat.css` | Pages 亮色主题产物 |
| `docs/index.html` | 引用新主题 |
| `.ai/project.md` | 更新变更日志 |

## 实施步骤
1. 使用浅灰蓝页面背景、白色面板、深蓝文字和亮蓝/绿色强调色。
2. 移除渐变背景与厚重阴影，卡片改为清晰边框和小圆角。
3. 保持汇总卡、单场、二串一、历史表格和响应式结构不变。
4. 桌面与 390px 移动视口检查可读性和横向溢出。

## 验证方式
- `python scripts/generate_pages.py`
- `python -m unittest discover -s tests`
- 浏览器桌面与移动视口视觉检查。

## 回滚方案
- 删除 `flat.css` 引用和生成逻辑即可恢复深色主题。

## 目标编辑文件清单
- `scripts/generate_pages.py`
- `docs/flat.css`
- `docs/index.html`
- `.ai/project.md`
