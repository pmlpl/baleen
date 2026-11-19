"""B 站扫码登录半自动获取 SESSDATA（2026-11-19，手动入口）。

设计（主人拍板）：被风控的 whisper 导入在 worker 里自动弹扫码窗（video_extractor 的
bilibili_login_cb 钩子）；这里给「视频笔记」页提供**手动**入口——提前扫好、超时后
重扫、也是扫码流程的独立测试口。

- ``POST /api/bilibili/login``：弹真浏览器扫码（同步阻塞，180s 等扫码 + 前置导航，
  vite proxy 300s 装得下），成功落 app_settings（热生效）并返回 uid。
- ``GET /api/bilibili/login``：当前登录态（是否已配置 + uid + 上次扫码时间）。
  永不回显 SESSDATA 全文（凭证只写不读）。
"""

from __future__ import annotations

import asyncio
from datetime import datetime

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from app.api.deps import CurrentOwnerDep, SessionDep
from app.repo.settings import SettingsRepo
from app.service.bilibili_login import (
    BiliLoginCancelled,
    BiliLoginError,
    BiliLoginTimeout,
    qr_login_bilibili,
    save_bilibili_login,
)

router = APIRouter(prefix="/api/bilibili", tags=["bilibili"])

# 等扫码时长上限（与 worker 自动弹窗一致；B 站二维码有效期也约 3 分钟）
QR_TIMEOUT_S = 180.0


class BiliLoginOut(BaseModel):
    logged_in: bool
    uid: str
    logged_at: datetime | None  # 未登录时为 None


@router.post("/login", response_model=BiliLoginOut)
async def bilibili_login(
    session: SessionDep, owner_id: CurrentOwnerDep
) -> BiliLoginOut:
    """弹真浏览器扫码登录 B 站，抓 SESSDATA 落库。同步阻塞，最长约 3.5 分钟。"""
    try:
        result = await asyncio.to_thread(qr_login_bilibili, QR_TIMEOUT_S)
    except BiliLoginCancelled as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except BiliLoginTimeout as exc:
        raise HTTPException(
            status_code=400,
            detail="等待扫码超时（180 秒）。再点一次本按钮会重新弹出扫码窗口。",
        ) from exc
    except BiliLoginError as exc:
        raise HTTPException(status_code=502, detail=f"B 站扫码登录失败：{exc}") from exc

    await save_bilibili_login(session, owner_id, result)
    await session.commit()
    return BiliLoginOut(logged_in=True, uid=result.uid, logged_at=result.logged_at)


@router.get("/login", response_model=BiliLoginOut)
async def bilibili_login_status(session: SessionDep, owner_id: CurrentOwnerDep) -> BiliLoginOut:
    """当前 B 站登录态（是否已配置 SESSDATA + uid + 上次扫码时间）。"""
    row = await SettingsRepo(session, owner_id).get()
    if row is None or not row.bilibili_sessdata:
        return BiliLoginOut(logged_in=False, uid="", logged_at=None)
    return BiliLoginOut(
        logged_in=True,
        uid=row.bilibili_uid or "",
        logged_at=row.bilibili_sessdata_at,
    )
