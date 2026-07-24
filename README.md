# rhythm-template-library

指板和弦指法枚举器：给定一个和弦与任意调弦的指板，枚举指板上所有能弹出该和弦的指法，并按「可演奏性」排序。

构建在 [`pytheory`](https://github.com/Zulko/pytheory) 之上。pytheory 内置的 `Fretboard.chord(name)` 只返回**标准调弦**下的一个默认指法，换调弦后查表失效，也不枚举所有可行 voicing。本库补足这一层：底层音高计算由 `Tone.transpose` 完成，与调弦无关，因此对任意调弦（DADGAD、尤克里里、完全自定义）都成立。

## 适用场景

- 节奏模板库：想要一组「吉他手实际会用的指法」作为节奏吉他素材
- 非标准调弦下的和弦查表：开放调弦、DADGAD、四弦乐器
- 指法可行性判定：哪些手型四根手指按得出来、哪些要横按、哪些物理上不可行

## 安装

需要 Python ≥ 3.13，使用 [`uv`](https://docs.astral.sh/uv/) 管理依赖。

```bash
uv sync
```

## 快速开始

```python
from fingering_enumerator import enumerate_fingerings
from pytheory import Fretboard

gtr = Fretboard.guitar()

# 枚举 C 大和弦的所有指法，按可演奏性升序返回
for f in enumerate_fingerings("C", gtr, max_fret=5, max_stretch=4, limit=5):
    print(f.positions, f.tones)
# 首选即经典开放 C 指法 (None, 3, 2, 0, 1, 0)
```

非标准调弦同样工作（pytheory 内置查表在此全部失效）：

```python
dad = Fretboard.guitar(tuning="dadgad")          # DADGAD
uk  = Fretboard.ukulele()                          # 尤克里里 GCEA
custom = Fretboard.guitar(tuning=("C4", "G3", "D3", "A2"))  # 完全自定义
```

## 核心概念

### 两种排序模型

`enumerate_fingerings` 的 `ranking` 参数：

- **`"playable"`（默认）** — 连续代价模型（`playability.playability_cost`）。把位高度、横按、内部闷音、空弦收益、省略和弦音等折算成同一尺度上的分数，**可交换**：低把位不再无条件压过一切，能为「少一次横按」让步。手指分配可行性作为**硬约束**，按不出来的手型直接剔除。结果更接近吉他手实际会用的指法。
- **`"legacy"`** — 旧的 `identify()` 硬检查 + `rank_key` 分层（字典序）排序。把位低优先于一切，不可交换。

### 手指分配可行性（硬约束）

`max_stretch` 跨度检查只能判断「手能不能张那么开」，判断不了「四根手指够不够用、绕不绕得过去」。`playability.plan_fingers` 把按弦位置真的分配给食/中/无名/小四根手指（可含食指横按与非食指小横按），分配不出来即视为弹不了。反例：

- `(1, 2, 3, 4, 5, None)` 跨度 4 合法，但要五根手指 → 弹不出
- `(1, 0, 3, 2, 1, None)` 同品两指之间夹着更高品，手指绕不过去 → 弹不出（真弹 F 只能靠食指横按）

### 和弦音省略

吉他只有六根弦、四根手指，扩展和弦装不下全部音。`playability.required_pitch_classes` 按吉他惯例给出可省音级：

- 三和弦不允许任何省略（省五音只剩根+三，识别度太低）
- 七和弦及以上允许省**完全五音**（对色彩贡献最小，且在根音泛音列里）
- 音数 ≥ 5 时十一音也可省（与三音相差半音，堆在一起浑浊）
- 减五、增五不在可省之列（它们是 `dim` / `aug` 的定性音）

`allow_omissions=True`（默认）时，开放 C7 `x32310`（缺五音 G）才会被找到。

### 原位与最低音判定

最低音一律用 `min(tones, key=midi)` 判定，**不依赖弦序**——自定义调弦下第 0 弦未必是最低音（如 `C4-G3-D3-A2`），只有比音高才不会把顶音弦错当成低音弦。

## 项目结构

```
fingering_enumerator.py  # 枚举器：笛卡尔积搜索 + 物理剪枝 + 排序
playability.py           # 可演奏性模型：手指分配 (硬约束) + 连续代价 (评分)
main.py                   # 演示 + 内联断言验证入口
tests/                    # pytest 测试
```

## 验证

`uv run main.py` 是本仓库的验证方式：每段演示都带 `assert`，断言失败即视为回归。核心回归是 `check_benchmark`——一组吉他教材里人人都会的常用指法（C / G / D / Am / F / C7 / Em7 ...）必须排进前 3 名。改 `fingering_enumerator.py` 或 `playability.py`（尤其调权重）后必须重跑。

```bash
uv run main.py        # 内联断言验证
uv run pytest         # 单元测试
```

代价权重提在 `playability.py` 顶部常量里，标定依据就是 `main.py` 的基准集。

## 依赖

- `pytheory >= 10.5` — 音高与指板计算
- `pytest >= 9.1`（dev）
