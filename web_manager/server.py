"""stdlib HTTP 服务器：节奏型模板 CRUD + 试听预览。

仅依赖 Python 标准库 + rhythm_pattern 公开 API。无 flask/fastapi/npm。路由：

- ``GET  /``                       → static/index.html
- ``GET  /static/<file>``          → app.js / styles.css
- ``GET  /api/templates``           → 全部模板（id + dict）
- ``GET  /api/templates/<id>``     → 单条模板
- ``POST /api/templates``           → 新建（body: {id, template}）
- ``PUT  /api/templates/<id>``      → 更新（body: template dict）
- ``DELETE /api/templates/<id>``    → 删除
- ``POST /api/preview``            → 试听音符列表（body: {template, chord, beats, bpm}）

启动::

    uv run python -m web_manager.server [--host 127.0.0.1] [--port 8000]
"""

from __future__ import annotations

import argparse
import json
import re
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from pytheory import Fretboard

from rhythm_pattern import TemplateRepository
from web_manager.adapter import (
    DbPatternSource,
    install_db_source,
    pattern_to_notelist,
    template_dict_to_pattern,
    template_to_dict,
)

_STATIC_DIR = Path(__file__).resolve().parent / "static"


class _Server:
    """共享状态：仓库 + 标准调弦 fretboard（启动时注入数据库源）。"""

    def __init__(self, repo: TemplateRepository) -> None:
        self.repo = repo
        self.fretboard = Fretboard.guitar()
        install_db_source(repo)


# ── 请求体 / 响应 工具 ────────────────────────────────────────────────


def _read_body(handler: BaseHTTPRequestHandler) -> bytes:
    length = int(handler.headers.get("Content-Length", 0) or 0)
    return handler.rfile.read(length) if length else b""


def _send_json(handler: BaseHTTPRequestHandler, payload: object, status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.send_header("Cache-Control", "no-store")
    handler.end_headers()
    handler.wfile.write(body)


def _send_text(handler: BaseHTTPRequestHandler, text: str, status: int, content_type: str) -> None:
    body = text.encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _send_file(handler: BaseHTTPRequestHandler, path: Path) -> None:
    if not path.is_file():
        _send_text(handler, "Not Found", 404, "text/plain; charset=utf-8")
        return
    body = path.read_bytes()
    ext = path.suffix.lower()
    ctype = {".html": "text/html; charset=utf-8", ".js": "application/javascript; charset=utf-8",
             ".css": "text/css; charset=utf-8"}.get(ext, "application/octet-stream")
    handler.send_response(200)
    handler.send_header("Content-Type", ctype)
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def _error(handler: BaseHTTPRequestHandler, msg: str, status: int = 400) -> None:
    _send_json(handler, {"error": msg}, status)


# ── API 路由 ──────────────────────────────────────────────────────────


def _list_templates(srv: _Server, handler: BaseHTTPRequestHandler, _id: str) -> None:
    items = [{"id": rid, **template_to_dict(p)} for rid, p in srv.repo.load()]
    _send_json(handler, {"templates": items})


def _get_template(srv: _Server, handler: BaseHTTPRequestHandler, id: str) -> None:
    p = srv.repo.get(id)
    if p is None:
        _error(handler, f"模板不存在: {id}", 404)
        return
    _send_json(handler, {"id": id, **template_to_dict(p)})


def _create_template(srv: _Server, handler: BaseHTTPRequestHandler, _id: str) -> None:
    try:
        data = json.loads(_read_body(handler).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        _error(handler, f"请求体 JSON 解析失败: {e}")
        return
    rid = data.get("id")
    if not rid:
        _error(handler, "缺少 id 字段")
        return
    # 支持 {id, template:{...}} 或 {id, ...模板字段} 两种载荷。
    tmpl = data.get("template", data)
    try:
        pattern = template_dict_to_pattern(tmpl)
    except (ValueError, TypeError, KeyError) as e:
        _error(handler, f"模板字段非法: {e}")
        return
    try:
        srv.repo.add(rid, pattern)
    except ValueError as e:
        _error(handler, str(e), 409)
        return
    _send_json(handler, {"id": rid, **template_to_dict(pattern)}, status=201)


def _update_template(srv: _Server, handler: BaseHTTPRequestHandler, id: str) -> None:
    try:
        tmpl = json.loads(_read_body(handler).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        _error(handler, f"请求体 JSON 解析失败: {e}")
        return
    try:
        pattern = template_dict_to_pattern(tmpl)
    except (ValueError, TypeError, KeyError) as e:
        _error(handler, f"模板字段非法: {e}")
        return
    try:
        srv.repo.update(id, pattern)
    except ValueError as e:
        _error(handler, str(e), 404 if "不存在" in str(e) else 409)
        return
    _send_json(handler, {"id": id, **template_to_dict(pattern)})


def _delete_template(srv: _Server, handler: BaseHTTPRequestHandler, id: str) -> None:
    ok = srv.repo.delete(id)
    if not ok:
        _error(handler, f"模板不存在: {id}", 404)
        return
    _send_json(handler, {"id": id, "deleted": True})


def _preview(srv: _Server, handler: BaseHTTPRequestHandler, _id: str) -> None:
    try:
        data = json.loads(_read_body(handler).decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        _error(handler, f"请求体 JSON 解析失败: {e}")
        return
    try:
        pattern = template_dict_to_pattern(data.get("template") or {})
    except (ValueError, TypeError, KeyError) as e:
        _error(handler, f"模板字段非法: {e}")
        return
    chord = str(data.get("chord") or "C")
    beats = int(data.get("beats") or 4)
    bpm = int(data.get("bpm") or 90)
    try:
        notelist = pattern_to_notelist(pattern, chord, srv.fretboard, beats, bpm)
    except (ValueError, TypeError) as e:
        _error(handler, f"实例化失败: {e}")
        return
    _send_json(handler, notelist)


# (method, regex) -> (handler, id_group)  id_group=0 表示无 id
_ROUTES: list[tuple[str, str, object, int]] = [
    ("GET", r"^/api/templates/?$", _list_templates, 0),
    ("GET", r"^/api/templates/(?P<id>[^/]+)/?$", _get_template, 1),
    ("POST", r"^/api/templates/?$", _create_template, 0),
    ("PUT", r"^/api/templates/(?P<id>[^/]+)/?$", _update_template, 1),
    ("DELETE", r"^/api/templates/(?P<id>[^/]+)/?$", _delete_template, 1),
    ("POST", r"^/api/preview/?$", _preview, 0),
]


class _Handler(BaseHTTPRequestHandler):
    server_version = "rhythm-web/0.1"

    def log_message(self, format: str, *args) -> None:  # noqa: A002 - 保持签名
        # 安静日志，避免刷屏；如需调试可改为 super().log_message(...)
        pass

    def _serve(self, method: str) -> None:
        path = urlparse(self.path).path
        # 静态资源
        if method == "GET" and path in ("", "/"):
            _send_file(self, _STATIC_DIR / "index.html")
            return
        if method == "GET" and path.startswith("/static/"):
            name = path[len("/static/"):]
            # 防目录穿越：只取文件名
            name = re.sub(r"[^A-Za-z0-9._-]", "", name)
            _send_file(self, _STATIC_DIR / name)
            return
        # API 路由
        for rmethod, pattern, func, _gid in _ROUTES:
            if rmethod != method:
                continue
            m = re.match(pattern, path)
            if not m:
                continue
            id_ = m.groupdict().get("id") if "id" in (m.groupdict() or {}) else ""
            # 路径里的 id 是 URL 编码的（空格/中文/括号等），需解码后再查库。
            if id_:
                id_ = unquote(id_)
            try:
                func(self.server.srv, self, id_ or "")
            except Exception:  # noqa: BLE001 - 顶层兜底，返回 500 不崩
                traceback.print_exc()
                _error(self, "服务器内部错误", 500)
            return
        _error(self, f"未知路由: {method} {path}", 404)

    def do_GET(self) -> None:
        self._serve("GET")

    def do_POST(self) -> None:
        self._serve("POST")

    def do_PUT(self) -> None:
        self._serve("PUT")

    def do_DELETE(self) -> None:
        self._serve("DELETE")


class _ServerHTTPServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr: tuple[str, int], srv: _Server) -> None:
        super().__init__(addr, _Handler)
        self.srv = srv  # _Handler.srv 通过实例属性访问


def main() -> None:
    parser = argparse.ArgumentParser(description="节奏型模板 web 管理器")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--db", default=None, help="templates.json 路径（默认包内 data/）")
    args = parser.parse_args()

    repo = TemplateRepository(args.db)
    if not repo.path.exists():
        from rhythm_pattern import seed_from_hardcoded

        print(f"数据库不存在，先 seed 到 {repo.path}")
        seed_from_hardcoded(repo.path)

    srv = _Server(repo)
    httpd = _ServerHTTPServer((args.host, args.port), srv)
    print(f"节奏型模板管理器: http://{args.host}:{args.port}")
    print(f"数据库: {repo.path}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
        httpd.shutdown()


if __name__ == "__main__":
    main()
