"""Web 管理器的写入路径回归。

重点钉住 `tags` 往返：编辑器的表单曾经不带这个字段，PUT 一次就把模板已有的标签
冲成空——而 tags 是选型的一维输入（musicnn 标签匹配），静默丢失会悄悄改变选型
结果。现在表单带上了该字段，服务端也加了「没给 tags 键 = 保留原值」的兜底。
"""

from __future__ import annotations

import io
import json

import pytest

from rhythm_pattern import StrumPattern, set_pattern_source
from rhythm_pattern.model import Stroke
from rhythm_pattern.serialization import TemplateRepository
from web_manager.server import _Server, _update_template


class _FakeHandler:
    """够 `_read_body` / `_send_json` 用的最小 handler 替身。"""

    def __init__(self, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.headers = {"Content-Length": str(len(body))}
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self.status: int | None = None
        self.sent_headers: list[tuple[str, str]] = []

    def send_response(self, status: int) -> None:
        self.status = status

    def send_header(self, key: str, value: str) -> None:
        self.sent_headers.append((key, value))

    def end_headers(self) -> None:
        pass

    @property
    def body(self) -> dict:
        return json.loads(self.wfile.getvalue().decode("utf-8"))


@pytest.fixture()
def srv(tmp_path):
    repo = TemplateRepository(tmp_path / "templates.json")
    repo.add(
        "alpha",
        StrumPattern(
            name="alpha",
            grid_motif=(Stroke("D", 4),),
            motif_beats=1, min_beats=1, ideal_beats=(1, 2),
            sections=("verse",), style="folk",
            tags=("guitar", "slow"),
        ),
    )
    server = _Server(repo)
    try:
        yield server
    finally:
        set_pattern_source(None)


def _payload(**overrides) -> dict:
    """一条合法的模板 dict（与 readForm() 输出同形）。tags 由调用方决定给不给。"""
    payload = {
        "name": "alpha",
        "technique": "strum",
        "style": "folk",
        "motif_beats": 1,
        "min_beats": 1,
        "time_signature": [4, 4],
        "ideal_beats": [1, 2],
        "sections": ["verse"],
        "positions": [],
        "grid_motif": [{"type": "stroke", "direction": "D", "duration": 4, "accent": "default"}],
    }
    payload.update(overrides)
    return payload


def test_update_without_tags_key_keeps_existing_tags(srv):
    """请求体缺 `tags` 键 → 保留原值（表单漏字段不再冲掉标签）。"""
    handler = _FakeHandler(_payload(name="alpha-renamed"))
    _update_template(srv, handler, "alpha")
    assert handler.status == 200, handler.body
    assert srv.repo.get("alpha").tags == ("guitar", "slow")


def test_update_with_explicit_tags_replaces_them(srv):
    """显式给 tags（列表）→ 照给的值写。"""
    handler = _FakeHandler(_payload(tags=["pop", "soft", "new age"]))
    _update_template(srv, handler, "alpha")
    assert handler.status == 200, handler.body
    assert srv.repo.get("alpha").tags == ("pop", "soft", "new age")


def test_update_with_empty_tags_clears_them(srv):
    """显式给空列表 → 清空（与「缺键 = 保留」区分开）。"""
    handler = _FakeHandler(_payload(tags=[]))
    _update_template(srv, handler, "alpha")
    assert handler.status == 200, handler.body
    assert srv.repo.get("alpha").tags == ()


def test_update_unknown_id_without_tags_still_404(srv):
    """id 不存在时照旧 404（缺 tags 的兜底不能把 404 变成别的错）。"""
    handler = _FakeHandler(_payload())
    _update_template(srv, handler, "ghost")
    assert handler.status == 404, handler.body
