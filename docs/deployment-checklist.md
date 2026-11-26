# 部署前必检清单（Deployment Checklist）

> 来源：2026-11-23 全库审查（`docs/review-2026-11-23.md`）B 级安全项，主人 2026-11-24 拍板
> 「毕设不公网部署」descope（裁决记录见 `docs/review-2026-11-23.md` §7）。
> **本清单的作用：一旦项目要部署到任何非本机环境（公网/局域网共享/演示服务器），
> 以下裁决全部自动重启，逐项处理完才能上线。**

## ⛔ 阻塞项（不处理不许上线）

| # | 项 | 现状 | 上线要求 | 位置 |
|---|---|---|---|---|
| B1 | 登录/注册无速率限制 | 未实现（裁决 descope） | 加限流：连续失败后指数退避或 IP+账号双维计数（可用现有 Redis 实现，不引新依赖） | `backend/app/api/auth.py` |
| B4 | jwt_secret 有开发默认值 | `"dev-only-insecure-secret-change-me"`（`backend/app/core/config.py:61`） | 启动时校验：非 DEBUG 环境且 secret 为默认值 → 拒绝启动 | `backend/app/core/config.py` |
| B2 | `GET /media/{key}` 无鉴权 | capability 模式（64 位 sha256 key 不可枚举，docstring 已写明取舍） | 二选一：① 接受 capability 模式并写入部署文档（key 不进日志/不进公开笔记）② 加 CurrentOwnerDep 鉴权 | `backend/app/api/library.py:447` |

## ⚠️ 建议项（上线前评估）

| # | 项 | 说明 | 位置 |
|---|---|---|---|
| S1 | CORS origins | 现为 `http://localhost:5173`；部署时改为真实域名，勿用 `*` | `backend/app/core/config.py` / `.env` |
| S2 | 上传魔数判头覆盖 | 已校验 pdf/docx/pptx/常见图片；新增格式时同步 `_MAGIC_PREFIXES` | `backend/app/api/upload.py:63` |
| S3 | 视频帧 10MB 预筛 | 与 A1 的 20MB media 闸并存属冗余保护层，无碍；调上限时两处一起改 | `backend/app/service/video_extractor.py:682` |
| S4 | arq cron 依赖 worker 存活 | 纸篓 30 天清理（每日 03:10）在 worker 进程里；部署后确认 worker 常驻，没起=不生效不报错 | `backend/app/worker/worker.py` |
| S5 | B 站 SESSDATA / 抖音 Playwright | 爬虫对抗功能属"尽力而为"，生产环境失败率会升高；失败路径已有用户提示，勿当 bug 修 | `backend/app/service/bilibili*.py` |

## 已做（部署时无需再处理）

- 上传扩展名白名单 + 首块魔数判头 + 50MB 流式限长（B3，2026-11-24 落地）
- CORS methods/headers 显式清单（B5，同日落地；新增 method 必须同步 `main.py` 清单 + `test_cors.py`）
- media 路径穿越防护（`is_media_key` 纯 hex 校验）+ 单张 20MB 上限（A1）
- 登录失败统一文案不泄露用户存在性；scrypt 参数内嵌哈希
- LLM/Tavily key 不进日志、不进异常文本

---

_维护约定：任何一项状态变化（裁决重启/实现/验证）都更新本表，并在 `STATUS.md` 速览留一行。_
