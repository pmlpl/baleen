"""GET /api/media/{key} 端点测试(v16):md 导入的图片按内容寻址取回。"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from app.service.media import store_media

PNG_BYTES = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d1f440000000049454e44ae426082"
)


def test_media_endpoint_serves_png(env: SimpleNamespace, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("app.service.media.MEDIA_ROOT", env.tmp_path / "media")
    key = store_media(PNG_BYTES)
    resp = env.client.get(f"/api/media/{key}")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("image/png")
    assert resp.content == PNG_BYTES


def test_media_endpoint_404(env: SimpleNamespace) -> None:
    assert env.client.get(f"/api/media/{'a' * 64}").status_code == 404
    assert env.client.get("/api/media/../secret").status_code == 404
