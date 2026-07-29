"""一次性 seed：把硬编码 STRUM_PATTERNS 导出到文本数据库 templates.json。

用法::

    uv run python scripts/seed_templates.py

导出后硬编码列表保留在代码里作为兜底默认源；web 管理器启动时会注入数据库源，
使编辑后的模板立即生效。重复运行会整表覆盖（幂等）。
"""

from __future__ import annotations

from rhythm_pattern.serialization import seed_from_hardcoded


def main() -> None:
    path = seed_from_hardcoded()
    print(f"已 seed 模板数据库到 {path}")


if __name__ == "__main__":
    main()
