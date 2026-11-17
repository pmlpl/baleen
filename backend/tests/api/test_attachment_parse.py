"""POST /api/parse 接口测试:📎 附件统一解析,不入库(env fixture 带真 token)。"""

from __future__ import annotations

import base64

PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d4944415478da63fcffff3f030005fe02fea72d1f440000000049454e44ae426082"
)


def _upload(env, name: str, content: bytes, mime: str = "application/octet-stream"):
    return env.client.post("/api/parse", files={"file": (name, content, mime)})


def test_parse_txt_full_mode(env) -> None:
    resp = _upload(env, "hello.txt", "第一行\n第二行".encode())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert data["mode"] == "full"
    assert "第一行" in data["text"]
    assert data["images"] == []


def test_parse_html_strips_base64_and_returns_image(env) -> None:
    b64 = base64.b64encode(PNG_1X1).decode()
    html = (
        '<html><body><p>姓名:张三</p>'
        f'<img src="data:image/png;base64,{b64}" alt="头像">'
        '</body></html>'
    )
    resp = _upload(env, "resume.html", html.encode())
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert "张三" in data["text"]
    assert "base64" not in data["text"]
    assert len(data["images"]) == 1
    assert data["images"][0]["mime"] == "image/png"
    assert data["images"][0]["data_url"].startswith("data:image/png;base64,")


def test_parse_doc_unsupported_message(env) -> None:
    resp = _upload(env, "old.doc", b"\xd0\xcf\x11\xe0")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "unsupported_format"
    assert "docx" in data["message"]


def test_parse_unknown_ext_415(env) -> None:
    resp = _upload(env, "thing.bin", b"MZ")
    assert resp.status_code == 415


def test_parse_over_limit_413(env) -> None:
    resp = _upload(env, "big.txt", b"x" * (10 * 1024 * 1024 + 1))
    assert resp.status_code == 413


def test_parse_no_token_401(env) -> None:
    saved = env.client.headers.pop("Authorization", None)
    try:
        resp = env.client.post("/api/parse", files={"file": ("a.txt", b"x", "text/plain")})
        assert resp.status_code == 401
    finally:
        if saved:
            env.client.headers["Authorization"] = saved
