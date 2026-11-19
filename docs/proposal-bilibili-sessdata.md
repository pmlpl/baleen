# 设计提案：B 站 SESSDATA 半自动扫码获取

> 立项来源：主人 2026-11-19 深夜提出，「被风控且未配置时自动弹浏览器让用户扫码登录一次，Playwright 抓到 SESSDATA 落盘，之后几个月免登录，过期再扫」。
> 状态：**只有提案，未改代码**。等主人拍板。
> 关联：`baleen-video-frame-extraction`（B 站 412 风控史）、`baleen-defects-ledger`（B 档：B 站风控复发）

## 一、现状与痛点

| 项 | 现状 |
|---|---|
| 读取位置 | 只有 `.env` 的 `BILIBILI_SESSDATA`（`config.py:72`），**设置页 / DB 都没有入口** |
| 获取方式 | 手工：bilibili.com → F12 → Application → Cookies → 复制 SESSDATA 值 → 贴进 `.env` → **重启后端** |
| 没配时 | `fetch_bilibili_media` 直接抛错，文案让用户去 F12 抄 |
| 实效 | 主人 11-19 两次实测：B 站版财富自由 yt-dlp 62s 超时 ×2 + 兜底被拦（`SESSDATA 未配置`），最后换抖音链接才拿到成品 |

**痛点本质**：F12 抄 cookie 是开发者操作，普通用户做不到；而且改 `.env` 还要重启后端，体验断成两截。

## 二、红线（先写清楚，避免方案跑偏）

| # | 红线 | 依据 |
|---|---|---|
| R1 | **SESSDATA 只给 Playwright 浏览器兜底用**（`bilibili_extractor.fetch_bilibili_media`），**不做 HTTP 层 cookie 重放** | 记忆实测：cookie 重放对风控无效，因为 B 站按 **TLS 指纹**打分；只有真实浏览器上下文才过 |
| R2 | **扫码等待必须有超时**（建议 3 分钟），超时按「未配置」原样报错，不挂死 worker | arq 任务 2h 超时，不能让用户忘关浏览器把任务拖死 |
| R3 | **不落明文进 git** | `.env` 已在 gitignore；若走 DB 则沿用现有 api_key 的存储约定（settings 表，接口只回掩码）|
| R4 | **不静默重试风控** | 已是 B 档教训：同视频反复下载会加速烧 IP 信誉。扫码失败就停，让用户决定 |

## 三、方案设计

### 3.1 触发时机

只在「B 站下载已被风控拦住，且当前没有 SESSDATA」时触发：

```
yt-dlp 路径失败（412/超时）
   ↓
有 SESSDATA？ ──有──→ 走 Playwright 兜底（现状）
   │没有
   ↓
【新增】弹扫码窗口（前端引导 + 后端起 Playwright 登录页）
   ↓
用户在窗口里扫码登录 → Playwright 抓到 SESSDATA
   ↓
落盘 → **自动续跑本次导入**（不用用户再点一次）
```

**不触发**的场景（避免打扰）：用户主动取消、抖音链接、已经配过 SESSDATA 只是过期（这时报「已过期，请重新扫码」）。

### 3.2 交互流程（前端）

| 步 | 用户看到 | 技术 |
|---|---|---|
| 1 | 导入进度卡片里出现一行：**「B 站风控拦截，需要登录一次」**+ 按钮【扫码登录】 | worker 在 `video_log` 追加一条 `stage="需要B站登录"`，前端按 stage 显示按钮 |
| 2 | 点按钮 → 弹出模态框：二维码（B 站登录页截图或官方二维码）+ 「用 B 站 App 扫码」 | 后端 `POST /api/sources/bilibili-login` 起一个 Playwright 会话，把登录页截图/二维码回传给前端 |
| 3 | 扫码成功后模态框自动关闭，卡片显示「登录成功，继续导入」 | 后端轮询检测 cookie 出现 → 落盘 → 自动重新入队 |
| 4 | 超时/取消 | 模态框显示「未完成登录，本次导入已停止。可稍后重试」 |

**为什么用截图而不是官方二维码 API**：B 站二维码是页面内 canvas/SVG，直接截登录页最稳（且顺带完成风控挑战）。截图尺寸 ~400×400，Base64 塞进 JSON 一次即可。

### 3.3 落盘位置：两个选项

| 选项 | 实现 | 优点 | 缺点 |
|---|---|---|---|
| **A. 写 DB（推荐）** | `app_settings` 加 `bilibili_sessdata` 字段（沿用 `llm_api_key` 的掩码约定）| 热生效（无需重启）、每用户独立、设置页可查看/清除 | 要加迁移 + 设置页字段 |
| B. 写 `.env` | 追加/替换 `BILIBILI_SESSDATA=` 行 | 零迁移 | **需要重启后端才生效**；多用户共用一份；`.env` 在用户机器上容易被误提交 |

推荐 A：与「每用户独立配置」的既有架构一致（多用户后 `llm_api_key` 就是这么做的），且热生效是这次体验改进的关键。

### 3.4 过期检测

SESSDATA 有效期通常数月，B 站可能在任一时刻让 cookie 失效。检测点：

- **被动检测（推荐）**：兜底打开视频页后仍被拦/要求登录 → 报「SESSDATA 已过期，请重新扫码」，同时**清掉 DB 里的值**（避免下次继续用坏 cookie 烧信誉）
- 主动检测（可选）：`GET /api/x/web-interface/nav` 判断 `isLogin`——多一次请求，收益小，先不做

## 四、实现拆解（主人拍板后按此做）

| # | 改动 | 文件 | 估时 |
|---|---|---|---|
| 1 | `app_settings` 加 `bilibili_sessdata` 列 + 迁移 | `models/setting.py`、`alembic/versions/00xx_*` | 0.5h |
| 2 | 配置解析：DB 值优先于 `.env` | `core/config.py` 或新增 `resolve_bilibili_sessdata(session, owner_id)` | 0.5h |
| 3 | 扫码端点：起 Playwright → 截图 → 轮询 cookie → 落 DB | `api/upload.py` 或新 `api/bilibili_login.py` | 2h |
| 4 | worker：风控失败 + 无 SESSDATA → 落 `stage="需要B站登录"` 并挂起（不失败退出） | `video_extractor.py` / `worker.py` | 1h |
| 5 | 前端：进度卡片按钮 + 扫码模态框 + 成功后自动续跑 | `VideoView.vue`、`sources.ts` | 2h |
| 6 | 过期检测 + 清坏 cookie | `bilibili_extractor.py` | 0.5h |
| 7 | 测试：登录端点（mock Playwright）、sessdata 解析优先级、过期清值 | `tests/api/test_bilibili_login.py` | 1.5h |

**总计约 8 小时**（一个完整工作日）。风险最高的是 #3（Playwright 在 API 进程里跑会不会阻塞——要放 `asyncio.to_thread`，且 worker 和 API 是两个进程，扫码会话应该放在 **API 进程**，worker 只负责等 DB 值出现）。

## 五、一个必须先验证的技术疑点

**Playwright 登录会话在 API 进程里跑，worker 在旁边等——两边能配合吗？**

可行设计：
1. worker 落 `stage="需要B站登录"` 到 `parse_meta`，然后**轮询 DB 等 `sessdata` 出现**（每 5s 一次，最多 3 分钟），超时则失败退出
2. API 进程的登录端点自己起 Playwright（`asyncio.to_thread`），扫码成功写 DB
3. worker 轮询到值 → 继续走兜底下载

这个设计不需要跨进程通信，只靠 DB 做信号——简单可靠。**但需要一次真实测试**：Playwright 在 uvicorn 进程里启动是否稳定（已知 worker 进程里是稳定的）。建议拍板后先写 20 行探针验证，再动生产代码。

## 六、要不要做：成本收益

| | |
|---|---|
| **收益** | B 站视频导入成功率从「风控就废」变成「扫码一次管几个月」；对毕设演示是可见的功能完整度提升 |
| **成本** | ~8 小时 + 一次真实扫码验证 |
| **风险** | B 站随时可能改登录流程/加强风控（外部依赖，B 档性质）|
| **替代方案** | 继续用抖音链接绕过（11-19 实测一次过）——零成本，但用户得自己找替代视频 |

**我的建议**：值得做，但**不是当前最优先**。理由：抖音替代路径已验证可用，而评测报告（论文）和渲染/配图才是毕设交付面。建议排在评测 backlog 之后，作为一个独立增量。
