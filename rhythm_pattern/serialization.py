"""节奏型模板的序列化 + 文本数据库仓库。

把 :class:`~rhythm_pattern.model.StrumPattern`（含 ``Stroke`` / ``Pluck(role)`` /
``None`` 栅格与弦角色）与 JSON 之间做往返转换，并落地到一个文本数据库
``rhythm_pattern/data/templates.json``，供 web 管理器增删改查。这样模板不再硬编码
在代码里，而是由文本数据库承载；硬编码的 :data:`STRUM_PATTERNS` 保留为兜底默认源
（见 :mod:`rhythm_pattern.strum_patterns` 的 ``set_pattern_source``）。

序列化只针对**模板**（``Pluck.strings`` 在模板层恒为 ``None``，不序列化）；实例化后的
栅格（弦号已填）不在序列化范围内。

设计要点
--------
- ``id`` 只存在于数据库记录，不进 ``StrumPattern`` 构造——核心模型零改动，集成项目
  完全无感。
- ``grid_motif`` 每格编码成 dict，``role`` 的构造参数（``region`` / ``n`` / ``span``）
  显式存储，故往返语义等价。
- 反序列化时重新构造 ``StrumPattern``，``__post_init__`` 的不变量校验自动复用——
  数据库里的非法记录在 load 阶段就炸，不会留到选型时。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from .model import Cell, Pluck, Stroke, StrumPattern
from .string_role import All, Fifth, Root, Seventh, StringRole, Third, TopN

if TYPE_CHECKING:
    pass


# ── 默认数据库路径（相对包定位，便于迁移）──────────────────────────────

_DEFAULT_DB_PATH = Path(__file__).resolve().parent / "data" / "templates.json"


# ── 弦角色 (de)serialization ──────────────────────────────────────────

# kind 名 → 构造闭包表。degree 不入库（kind 名即承载音级）。
_DEGREE_KINDS: dict[str, type[StringRole]] = {
    "root": Root,
    "third": Third,
    "fifth": Fifth,
    "seventh": Seventh,
}


def _role_to_dict(role: StringRole | None) -> dict | None:
    """把弦角色编码成 dict（``None`` 角色 → JSON ``null``）。"""
    if role is None:
        return None
    if isinstance(role, (Root, Third, Fifth, Seventh)):
        kind = {Root: "root", Third: "third", Fifth: "fifth", Seventh: "seventh"}[
            type(role)
        ]
        return {"kind": kind, "region": role.region}
    if isinstance(role, TopN):
        return {"kind": "topn", "n": role.n, "span": role.span}
    if isinstance(role, All):
        return {"kind": "all"}
    raise TypeError(f"不可序列化的弦角色类型: {type(role).__name__}")


_VALID_REGIONS = {None, "bass", "treble", "avoid_bass"}
_VALID_SPANS = {None, "comfortable", "narrow"}


def _role_from_dict(d: dict | None) -> StringRole | None:
    """从 dict 还原弦角色。``None`` / 缺省 → ``None``。

    校验 ``region`` / ``span`` 取值合法，非法值在 load 阶段即抛错（不静默降级），
    兑现「数据库非法记录在 load 时炸」的承诺。
    """
    if d is None:
        return None
    kind = d.get("kind")
    if kind in _DEGREE_KINDS:
        region = d.get("region")
        if region not in _VALID_REGIONS:
            raise ValueError(f"非法 region 值: {region!r}")
        return _DEGREE_KINDS[kind](region)
    if kind == "topn":
        n = d.get("n")
        if not isinstance(n, int) or isinstance(n, bool) or n < 1:
            raise ValueError(f"非法 topn n 值: {n!r}")
        span = d.get("span")
        if span not in _VALID_SPANS:
            raise ValueError(f"非法 span 值: {span!r}")
        return TopN(n, span)
    if kind == "all":
        return All()
    raise ValueError(f"未知的弦角色 kind: {kind!r}")


# ── 栅格 Cell (de)serialization ────────────────────────────────────────


def _cell_to_dict(cell: Cell) -> dict:
    """把一格栅格编码成 dict。

    - ``Stroke`` → ``{"type": "stroke", "direction": "D"/"U"}``
    - ``None`` → ``{"type": "rest"}``
    - ``Pluck`` → ``{"type": "pluck", "role": <role dict | null>}``（``strings`` 不存）
    """
    if cell is None:
        return {"type": "rest"}
    if isinstance(cell, Stroke):
        return {"type": "stroke", "direction": cell.direction}
    if isinstance(cell, Pluck):
        return {"type": "pluck", "role": _role_to_dict(cell.role)}
    raise TypeError(f"不可序列化的栅格类型: {type(cell).__name__}")


def _cell_from_dict(d: dict) -> Cell:
    """从 dict 还原一格栅格。"""
    t = d.get("type")
    if t == "rest":
        return None
    if t == "stroke":
        return Stroke(d["direction"])
    if t == "pluck":
        return Pluck(role=_role_from_dict(d.get("role")))
    raise ValueError(f"未知的栅格 type: {t!r}")


# ── StrumPattern (de)serialization ─────────────────────────────────────


def pattern_to_dict(pattern: StrumPattern) -> dict:
    """把一个模板编码成 dict（**不含** ``id``，``id`` 由仓库层管理）。

    ``strings`` 字段不出现（模板层恒为 ``None``）。返回的 dict 可直接 ``json.dumps``，
    配合一个顶层 ``id`` 字段即成数据库记录。
    """
    return {
        "name": pattern.name,
        "grid_motif": [_cell_to_dict(c) for c in pattern.grid_motif],
        "motif_beats": pattern.motif_beats,
        "min_beats": pattern.min_beats,
        "ideal_beats": list(pattern.ideal_beats),
        "sections": list(pattern.sections),
        "style": pattern.style,
        "technique": pattern.technique,
        "positions": list(pattern.positions),
    }


def dict_to_pattern(d: dict) -> StrumPattern:
    """从 dict 还原 :class:`StrumPattern`。

    重新构造 dataclass，``__post_init__`` 的不变量校验自动复用——数据库里的非法记录
    在此炸，不会留到选型时。``id`` 字段（若有）被忽略，不进构造。
    """
    return StrumPattern(
        name=d["name"],
        grid_motif=tuple(_cell_from_dict(c) for c in d["grid_motif"]),
        motif_beats=d["motif_beats"],
        min_beats=d["min_beats"],
        ideal_beats=tuple(d.get("ideal_beats", ())),
        sections=tuple(d.get("sections", ())),
        style=d["style"],
        technique=d.get("technique", "strum"),
        positions=tuple(d.get("positions", ())),
    )


# ── 文本数据库仓库 ────────────────────────────────────────────────────


class TemplateRepository:
    """``templates.json`` 的读写仓库，提供模板增删改查。

    仓库是数据库文件的**唯一写者**；读时每条记录经 :func:`dict_to_pattern` 重新构造
    ``StrumPattern``，非法记录在 load 阶段就抛错。``id`` 是稳定主键（不可变），
    ``name`` 亦强制唯一（选型器 fallback 按 name 查 ``boom-chick`` 依赖此性质）。

    Parameters
    ----------
    path
        数据库文件路径。默认 ``rhythm_pattern/data/templates.json``（相对包定位）。
    """

    def __init__(self, path: Path | str | None = None) -> None:
        self._path = Path(path) if path is not None else _DEFAULT_DB_PATH

    @property
    def path(self) -> Path:
        """数据库文件路径。"""
        return self._path

    # ── 读写文件 ──

    def _read_raw(self) -> list[dict]:
        """读原始记录列表；文件不存在时返回空列表。"""
        if not self._path.exists():
            return []
        with self._path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, list):
            raise ValueError(f"数据库 {self._path} 顶层应为数组，实际 {type(data).__name__}")
        return data

    def _write_raw(self, records: list[dict]) -> None:
        """写原始记录列表（美化 + 保留中文）。"""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._path.open("w", encoding="utf-8") as f:
            json.dump(records, f, ensure_ascii=False, indent=2)

    # ── 查询 ──

    def load(self) -> list[tuple[str, StrumPattern]]:
        """加载全部模板，返回 ``(id, StrumPattern)`` 列表。每条经构造校验。"""
        out: list[tuple[str, StrumPattern]] = []
        for rec in self._read_raw():
            rid = rec.get("id")
            if not rid:
                raise ValueError(f"数据库记录缺 id: {rec.get('name')!r}")
            out.append((rid, dict_to_pattern(rec)))
        return out

    def get(self, id: str) -> StrumPattern | None:
        """按 ``id`` 取单条；不存在返回 ``None``。"""
        for rid, pat in self.load():
            if rid == id:
                return pat
        return None

    def get_by_name(self, name: str) -> StrumPattern | None:
        """按 ``name`` 取单条；不存在返回 ``None``。"""
        for _, pat in self.load():
            if pat.name == name:
                return pat
        return None

    # ── 增删改 ──

    def add(self, id: str, pattern: StrumPattern) -> None:
        """新增一条 ``(id, pattern)``。重 ``id`` 或重 ``name`` 报错。"""
        records = self._read_raw()
        existing_ids = {r.get("id") for r in records}
        existing_names = {r.get("name") for r in records}
        if id in existing_ids:
            raise ValueError(f"id 已存在: {id!r}")
        if pattern.name in existing_names:
            raise ValueError(f"name 已存在: {pattern.name!r}")
        records.append({"id": id, **pattern_to_dict(pattern)})
        self._write_raw(records)

    def update(self, id: str, pattern: StrumPattern) -> None:
        """更新 ``id`` 对应记录（``id`` 不变，模板字段替换）。缺 id 报错；
        新 name 与其他记录冲突报错。"""
        records = self._read_raw()
        idx = next((i for i, r in enumerate(records) if r.get("id") == id), None)
        if idx is None:
            raise ValueError(f"id 不存在: {id!r}")
        # name 唯一性：跳过自身那条。
        for i, r in enumerate(records):
            if i != idx and r.get("name") == pattern.name:
                raise ValueError(f"name 已存在: {pattern.name!r}")
        records[idx] = {"id": id, **pattern_to_dict(pattern)}
        self._write_raw(records)

    def delete(self, id: str) -> bool:
        """删除 ``id`` 对应记录；返回是否删除成功。"""
        records = self._read_raw()
        new = [r for r in records if r.get("id") != id]
        if len(new) == len(records):
            return False
        self._write_raw(new)
        return True

    def save_all(self, items: list[tuple[str, StrumPattern]]) -> None:
        """整表覆盖写入 ``(id, pattern)`` 列表。用于 seed / 批量导入。

        不做重 id / 重 name 检查（调用方负责），但会去重保留最后一条。
        """
        seen: dict[str, dict] = {}
        for rid, pat in items:
            seen[rid] = {"id": rid, **pattern_to_dict(pat)}
        self._write_raw(list(seen.values()))


# ── 从硬编码库 seed ──────────────────────────────────────────────────


def seed_from_hardcoded(path: Path | str | None = None) -> Path:
    """把硬编码 :data:`STRUM_PATTERNS` 全量导出到文本数据库（一次性 seed）。

    ``id`` 直接取 ``name``（初始模板名已是唯一可读 slug）。硬编码列表**保留**在代码里
    作为兜底默认源，不删除。返回写入的文件路径。

    Parameters
    ----------
    path
        目标文件路径；默认 :data:`_DEFAULT_DB_PATH`。
    """
    from .strum_patterns import STRUM_PATTERNS

    repo = TemplateRepository(path)
    repo.save_all([(p.name, p) for p in STRUM_PATTERNS])
    return repo.path


def _main() -> None:
    """``python -m rhythm_pattern.serialization --seed`` 入口。"""
    import argparse

    parser = argparse.ArgumentParser(description="节奏型模板数据库工具")
    parser.add_argument("--seed", action="store_true", help="从硬编码库导出模板到 JSON")
    parser.add_argument("--path", default=None, help="数据库文件路径")
    args = parser.parse_args()
    if args.seed:
        p = seed_from_hardcoded(args.path)
        n = len(TemplateRepository(p).load())
        print(f"已 seed {n} 个模板到 {p}")
        return
    parser.print_help()


if __name__ == "__main__":
    _main()
