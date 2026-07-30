"""节奏型模板 web 管理器（独立顶层包，与 rhythm_pattern 低耦合）。

通过 ``uv run python -m web_manager.server`` 启动。仅依赖 Python 标准库 +
rhythm_pattern 公开 API；试听用浏览器 Web Audio 合成，服务端只返回 JSON 音符列表。
"""
