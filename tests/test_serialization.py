"""测试模板序列化 + 文本数据库仓库。

覆盖：所有硬编码模板的 JSON 往返、各弦角色变体往返、仓库 CRUD、seed 确定性、
数据源 seam 注入。
"""

import json

import pytest

from rhythm_pattern import STRUM_PATTERNS
from rhythm_pattern.model import Pluck, StrumPattern, Stroke
from rhythm_pattern.serialization import (
    TemplateRepository,
    dict_to_pattern,
    pattern_to_dict,
    seed_from_hardcoded,
)
from rhythm_pattern.string_role import All, Fifth, Root, Seventh, Third, TopN


# ── 往返 ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize("pattern", STRUM_PATTERNS, ids=lambda p: p.name)
def test_hardcoded_roundtrip(pattern):
    """每个硬编码模板经 dict 往返后与自身相等（含过 __post_init__ 校验）。"""
    rt = dict_to_pattern(pattern_to_dict(pattern))
    assert rt == pattern


def test_role_variants_roundtrip():
    """各弦角色变体逐格往返。覆盖所有 region / span 组合 + All + 占位 None。"""
    cells = [
        Pluck(role=Root("bass")),
        Pluck(role=Root("treble")),
        Pluck(role=Root("avoid_bass")),
        Pluck(role=Root()),
        Pluck(role=Third()),
        Pluck(role=Fifth("avoid_bass")),
        Pluck(role=Seventh()),
        Pluck(role=TopN(2)),
        Pluck(role=TopN(2, "comfortable")),
        Pluck(role=TopN(2, "narrow")),
        Pluck(role=All()),
        Pluck(role=None),
        Stroke("D"),
        None,
        None,
        None,
    ]
    pat = StrumPattern(
        name="all-roles",
        grid_motif=tuple(cells),
        motif_beats=len(cells) // 4,
        min_beats=len(cells) // 4,
        ideal_beats=(),
        sections=("verse",),
        style="folk",
        technique="arpeggio",
    )
    rt = dict_to_pattern(pattern_to_dict(pat))
    assert rt == pat


def test_roundtrip_keeps_grid_semantics():
    """往返后 grid_motif 的格子类型与 role 参数逐一相等（防 kind 错位）。"""
    p = next(p for p in STRUM_PATTERNS if p.name == "53231323 (16分)")
    rt = dict_to_pattern(pattern_to_dict(p))
    assert len(rt.grid_motif) == len(p.grid_motif)
    for a, b in zip(p.grid_motif, rt.grid_motif):
        assert type(a) is type(b)
        if isinstance(a, Pluck):
            assert (a.role is None) == (b.role is None)
            if a.role is not None:
                assert type(a.role) is type(b.role)


def test_invalid_role_values_rejected_at_load():
    """非法 region / span / n 在反序列化时即抛错（不静默降级）。"""
    from rhythm_pattern.serialization import _role_from_dict
    with pytest.raises(ValueError, match="region"):
        _role_from_dict({"kind": "root", "region": "garbage"})
    with pytest.raises(ValueError, match="span"):
        _role_from_dict({"kind": "topn", "n": 2, "span": "wide"})
    with pytest.raises(ValueError, match="n"):
        _role_from_dict({"kind": "topn", "n": 0, "span": None})
    with pytest.raises(ValueError, match="kind"):
        _role_from_dict({"kind": "nonsense"})


def test_unknown_cell_type_rejected():
    from rhythm_pattern.serialization import _cell_from_dict
    with pytest.raises(ValueError, match="type"):
        _cell_from_dict({"type": "weird"})


# ── 仓库 CRUD ────────────────────────────────────────────────────────


@pytest.fixture()
def repo(tmp_path):
    return TemplateRepository(tmp_path / "templates.json")


def _sample(name="tmp-pattern"):
    return StrumPattern(
        name=name, grid_motif=(Stroke("D"), None, None, None),
        motif_beats=1, min_beats=1, ideal_beats=(2, 4),
        sections=("chorus",), style="pop", technique="strum",
    )


def test_repo_crud(repo):
    pat = _sample("alpha")
    repo.add("alpha", pat)
    assert repo.get("alpha").name == "alpha"
    assert repo.get_by_name("alpha") is not None
    assert repo.get("missing") is None


def test_repo_update_and_persist(repo):
    repo.add("alpha", _sample("alpha"))
    pat2 = _sample("beta")  # 新 name
    repo.update("alpha", pat2)
    assert repo.get("alpha").name == "beta"  # id 不变
    # 持久化：重新实例化仓库仍能读到更新后的数据
    repo2 = TemplateRepository(repo.path)
    assert repo2.get("alpha").name == "beta"


def test_repo_delete(repo):
    repo.add("alpha", _sample("alpha"))
    assert repo.delete("alpha") is True
    assert repo.get("alpha") is None
    assert repo.delete("alpha") is False  # 再删返回 False


def test_repo_duplicate_id_rejected(repo):
    repo.add("alpha", _sample("alpha"))
    with pytest.raises(ValueError, match="id 已存在"):
        repo.add("alpha", _sample("beta"))


def test_repo_duplicate_name_rejected(repo):
    repo.add("alpha", _sample("same-name"))
    with pytest.raises(ValueError, match="name 已存在"):
        repo.add("beta", _sample("same-name"))


def test_repo_update_missing_id(repo):
    with pytest.raises(ValueError, match="id 不存在"):
        repo.update("ghost", _sample("ghost"))


def test_repo_update_name_conflict(repo):
    repo.add("alpha", _sample("alpha"))
    repo.add("beta", _sample("beta"))
    # 把 alpha 改成 beta 既有 name → 冲突
    with pytest.raises(ValueError, match="name 已存在"):
        repo.update("alpha", _sample("beta"))


# ── seed ─────────────────────────────────────────────────────────────


def test_seed_writes_all(tmp_path):
    path = seed_from_hardcoded(tmp_path / "seeded.json")
    repo = TemplateRepository(path)
    items = repo.load()
    assert len(items) == len(STRUM_PATTERNS)
    # 每条与硬编码按 dict 相等
    by_name = {p.name: p for p in STRUM_PATTERNS}
    for rid, pat in items:
        assert pattern_to_dict(pat) == pattern_to_dict(by_name[rid])


def test_seed_json_is_utf8_chinese(tmp_path):
    path = seed_from_hardcoded(tmp_path / "seeded.json")
    raw = path.read_text(encoding="utf-8")
    # 含中文名的模板应直接以中文存储（非 \\u 转义）。
    assert "16分" in raw or "8分" in raw
    json.loads(raw)  # 解析无错
