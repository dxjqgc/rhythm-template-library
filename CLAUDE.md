# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目性质

在 `pytheory` 之上补一层**指板和弦指法枚举器**。pytheory 自带的 `Fretboard.chord(name)` 只返回标准调弦下的一个查表指法，换调弦即失效，也不枚举所有 voicing；本仓库用组合搜索解决这个问题，对任意调弦（含 DADGAD、尤克里里、自定义弦序）都成立。

仓库名是 `rhythm-template-library`，但目前只落了指法枚举这一块；`music21` 已在依赖里但尚未被任何代码引用。

## 环境与命令

用 uv 管理，Python 3.13（`.python-version`），`uv.lock` 被 gitignore。

```bash
uv sync                 # 安装依赖（首次 uv run 会自动建 .venv）
uv run main.py          # 运行演示 + 内联断言（当前唯一的验证入口）
```

**没有测试框架、没有 lint 配置。** 验证靠 `main.py` 里的 `assert`（F 横按指头数 = 4、冗余大拇指判据区分 `(5,0,2,2,2,0)` 与 `x02220`）以及输出中 “经典开放 C 指法 (x32010) 是否找到: True”。改动 `fingering_enumerator.py` 后必须跑一遍 `uv run main.py`，确认断言不炸且排序结果里经典指法仍排在前列。

新增判据时的惯例是往 `main.py` 里加一段带 `assert` 的演示，而不是引入 pytest。

## 架构要点

`fingering_enumerator.py` 是全部逻辑，单模块，四个公开函数分工明确：

- `enumerate_fingerings()` —— 主入口。对每根弦列出 `0..max_fret` 中落在和弦音级上的把位（外加 `None` 闷音选项），做笛卡尔积，逐个过**硬过滤**，最后按 `rank_key` 排序。
- `rank_key()` —— 排序键，**分层字典序**：`(原位/转位, 最低把位, -发音弦数, 横按感知指头数, 跨度)`。刻意不用线性加权，因为加权会让“凑满 6 弦的高把位横按”压过低把位开放指法。
- `analyze_barre()` —— 把横按识别成 1 个手指。判据：最低按弦品上至少 2 根弦，且这些弦构成的**连续区间内**不得出现闷音/空弦/更低品。
- `is_redundant_thumb()` —— 排除需要大拇指绕琴颈、且闷掉后仍完整的低音弦孤立高把位（如 A 和弦的 `(5,0,2,2,2,0)`）。

硬过滤（在 `strict=True` 下）依次是：跨度 ≤ `max_stretch` → 含根音 → 音级集合**超集检查**（`target_pcs.issubset(present_pcs)`，防止 E sus4 被 `identify()` 之外的路径当成 A major）→ `identify()` 非空且非 power chord → 非冗余大拇指。**过滤与排序是两套机制**：能不能弹归硬过滤，好不好弹归 `rank_key`。

## 改动时必须守住的约定

- `positions` 序列按**弦号**排列（`Fretboard.tones` 同序），`None` = 闷音，`0` = 空弦。自定义调弦下第 0 弦未必是最低音。
- 凡是判断“最低音 / 原位”，一律用 `min(tones, key=lambda t: t.midi)`，**绝不用弦序索引**。这是自定义调弦能正确工作的前提。
- 音高类统一走 `_pitch_class()`（`tone.midi % 12`）——pytheory 的 `Tone` 没有 `pitch_class` 属性。
- `score_fingering()` 是早期的线性加权评分，**已不参与排序**（`enumerate_fingerings` 只用 `rank_key`），仍在 `__all__` 里对外暴露。改排序逻辑时动 `rank_key`，不要误改它。
- 搜索空间是弦数的指数级，`max_fret` 和 `max_stretch` 是唯一的规模控制手段；演示里用 `max_fret=5..7`。放宽这两个值前先估算组合数。

## 文档风格

模块内注释和 docstring 全部是中文，且写“为什么这么判”而非“这行做什么”——每个判据都附了它要避开的具体退化案例（F 横按误算 6 指、A 和弦大拇指冗余、E sus4 误识别）。新增逻辑请沿用这个密度：给出判据 + 反例。
