# 📡 AstrBot RSS 订阅插件

`astrbot_plugin_rss` — 为 [AstrBot](https://github.com/AstrBotDevs/AstrBot) 提供 RSS/Atom 订阅能力。

订阅你喜欢的博客、新闻、技术文章源，插件会**定时检查更新**并**自动推送**新内容到你的聊天窗口。

## ✨ 功能

- ✅ **订阅 RSS/Atom 源** — 支持标准 RSS 2.0 / Atom 格式
- ✅ **自动轮询** — 后台定时检查更新，无需手动操作
- ✅ **智能去重** — 已推送的条目不会重复推送
- ✅ **多会话订阅** — 同一个 RSS 源可以推送到多个群/私聊
- ✅ **可调间隔** — 每个源单独设置检查频率（5分钟~24小时）
- ✅ **国际化** — 支持中/英文界面
- ✅ **WebUI 配置** — 在 AstrBot 管理面板可视化修改设置

## 📋 命令

| 命令 | 说明 |
|------|------|
| `/rss add <url>` | 订阅一个 RSS/Atom 源 |
| `/rss remove <id>` | 取消订阅 |
| `/rss list` | 查看所有订阅 |
| `/rss set <id> interval <N>` | 设置检查间隔（分钟） |
| `/rss help` | 显示帮助 |

## 🚀 安装

### 方式一：AstrBot 插件市场（推荐）
在 AstrBot WebUI → 插件市场 → 搜索 `rss` → 一键安装。

### 方式二：手动安装
```bash
# 进入 AstrBot 的插件目录
cd data/plugins/

# 克隆本仓库
git clone https://github.com/ownisjiang/astrbot_plugin_rss.git

# 安装依赖
pip install feedparser httpx

# 重启 AstrBot
```

### 方式三：直接复制
将 `astrbot_plugin_rss/` 文件夹复制到 AstrBot 的 `data/plugins/` 目录下，重启 AstrBot。

## ⚙️ 配置

在 AstrBot WebUI → 插件设置 → RSS 订阅器 中可配置：

| 配置项 | 类型 | 默认值 | 说明 |
|--------|------|--------|------|
| `default_interval` | int | 30 | 新订阅默认检查间隔（分钟） |
| `max_entries_per_push` | int | 5 | 每次最多推送的新条目数 |

## 🧪 示例

```text
# 订阅一个 RSS 源
/rss add https://news.ycombinator.com/rss

# 输出：
✅ 订阅成功！
📰 Hacker News
🆔 ID: a1b2c3d4
⏱ 检查间隔: 30 分钟
📌 新内容将自动推送至此会话

# 查看订阅列表
/rss list

# 设置检查间隔为 15 分钟
/rss set a1b2c3d4 interval 15

# 取消订阅
/rss remove a1b2c3d4
```

## 📁 文件结构

```
astrbot_plugin_rss/
├── main.py                  # 插件主代码
├── metadata.yaml            # 插件元数据
├── _conf_schema.json        # 配置 UI 结构
├── requirements.txt         # Python 依赖
└── .astrbot-plugin/
    └── i18n/
        ├── zh-CN.json       # 中文翻译
        └── en-US.json       # 英文翻译
```

## 📦 依赖

- `feedparser` — RSS/Atom 解析
- `httpx` — 异步 HTTP 请求

## 🔧 开发计划 / 未来功能

- [ ] 导出/导入订阅列表（OPML 格式）
- [ ] 支持关键词过滤推送
- [ ] 支持全文提取（ readability ）
- [ ] 插件市场自动更新

## 📝 许可证

AGPL-3.0
