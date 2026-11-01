"""CSV 解析单测:编码探测/分隔符嗅探/补齐/分表/空文件,全部 tmp_path 自造,不依赖 fixture。"""

from __future__ import annotations

from pathlib import Path

from app.service.parse import ParseStatus, parse_file
from app.service.parse.csv_parser import _ROWS_PER_TABLE, parse_csv
from app.service.parse.router import PARSERS


def _cells(table: dict) -> list[list[str]]:
    return [
        [c["content"][0]["content"][0]["text"] if c["content"] else "" for c in row["content"]]
        for row in table["content"]
    ]


def _tables(doc: dict | None) -> list[dict]:
    if doc is None:
        return []
    return [b for b in doc["content"] if b["type"] == "table"]


def test_csv_basic_header_and_cells(tmp_path: Path) -> None:
    p = tmp_path / "t.csv"
    p.write_text("名称,数量,备注\n螺丝,100,标准件\n轴承,25,精密件\n", encoding="utf-8")
    r = parse_csv(p)
    assert r.parse_status == ParseStatus.OK
    tables = _tables(r.blocks)
    assert len(tables) == 1
    rows = _cells(tables[0])
    assert rows[0] == ["名称", "数量", "备注"]
    assert rows[1] == ["螺丝", "100", "标准件"]
    assert tables[0]["content"][0]["content"][0]["type"] == "tableHeader", "首行按表头"
    assert tables[0]["content"][1]["content"][0]["type"] == "tableCell"
    assert r.meta["rows"] == 3 and r.meta["cols"] == 3
    assert r.warnings == []


def test_csv_gb18030_decoded(tmp_path: Path) -> None:
    p = tmp_path / "gbk.csv"
    p.write_bytes("零件,单价\n螺栓,3.5\n".encode("gb18030"))
    r = parse_csv(p)
    assert r.parse_status == ParseStatus.OK
    assert any("GB18030" in w for w in r.warnings)
    assert "零件" in _cells(_tables(r.blocks)[0])[0], "中文没被解码坏"


def test_csv_semicolon_sniffed(tmp_path: Path) -> None:
    p = tmp_path / "semi.csv"
    p.write_text("a;b\n1;2\n", encoding="utf-8")
    r = parse_csv(p)
    rows = _cells(_tables(r.blocks)[0])
    assert rows[0] == ["a", "b"], "分号分隔被嗅探并正确拆列"
    assert rows[1] == ["1", "2"]
    assert r.meta["delimiter"] == ";"


def test_csv_ragged_rows_padded(tmp_path: Path) -> None:
    p = tmp_path / "ragged.csv"
    p.write_text("a,b,c\n1\nx,y\n", encoding="utf-8")
    r = parse_csv(p)
    rows = _cells(_tables(r.blocks)[0])
    assert all(len(row) == 3 for row in rows), "短行补空格对齐"
    assert rows[1] == ["1", "", ""]


def test_csv_empty_file_ok(tmp_path: Path) -> None:
    p = tmp_path / "empty.csv"
    p.write_bytes(b"")
    r = parse_csv(p)
    assert r.parse_status == ParseStatus.OK
    assert r.blocks is None or r.blocks.get("content") == []
    assert r.meta["chars"] == 0


def test_csv_only_blank_lines_ok(tmp_path: Path) -> None:
    p = tmp_path / "blank.csv"
    p.write_text("\n,\n\n", encoding="utf-8")
    r = parse_csv(p)
    assert r.parse_status == ParseStatus.OK
    assert _tables(r.blocks) == [], "全空行不产表格块"


def test_csv_split_into_tables_over_cap(tmp_path: Path) -> None:
    n = _ROWS_PER_TABLE + 50
    p = tmp_path / "big.csv"
    lines = ["idx,name"] + [f"{i},行{i}" for i in range(n - 1)]
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    r = parse_csv(p)
    tables = _tables(r.blocks)
    assert len(tables) == 2, f"{n} 行应拆成两块(每块 {_ROWS_PER_TABLE} 行)"
    assert len(tables[0]["content"]) == _ROWS_PER_TABLE
    assert len(tables[1]["content"]) == 50
    assert tables[0]["content"][0]["content"][0]["type"] == "tableHeader"
    assert tables[1]["content"][0]["content"][0]["type"] == "tableCell", "只有第一块首行是表头"


def test_router_routes_csv(tmp_path: Path) -> None:
    p = tmp_path / "via_router.csv"
    p.write_text("h1,h2\nv1,v2\n", encoding="utf-8")
    r = parse_file(p)
    assert r.parse_status == ParseStatus.OK
    assert r.meta["engine"] == "csv"
    assert ".csv" in PARSERS


def test_upload_allowlist_includes_csv() -> None:
    from app.api.upload import ALLOWED_EXTS

    assert ".csv" in ALLOWED_EXTS, "上传 API 的放行清单应随 PARSERS 自动带上 .csv"
