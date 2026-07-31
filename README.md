# rhythm-template-library

吉他弹唱的**节奏型模板库 + 自动选型/编排**，配套**指法枚举器**与**浏览器试听的 Web 管理器**。

给一段和弦进行（每个和弦占几拍）与段落/风格，为每个和弦选出一个合适的节奏型，输出 16 分音符栅格 + 指法动作序列（扫弦为下扫/上扫、分解为拨弦带具体弦号）。选型把拍数门槛、段落契合、目标密度、技法基线、BPM、位置、进行级连贯性等折算成同一个尺度上的连续代价，越小越靠前。

构建在 [`pytheory`](https://github.com/Zulko/pytheory) 之上，对**任意调弦**中立——指法枚举的音高计算与调弦无关，分解模板用弦角色（音级）表达弦序、选型时按当前和弦 voicing 实例化成具体弦号，换调弦自动重映射。

## 三个组成部分

| 子包 | 作用 |
|------|------|
| `rhythm_pattern/` | 核心：节奏型模板库 + 选型器 + 整段编排（扫弦 + 分解） |
| `chord_fingering/` | 基础库：和弦指法枚举与可演奏性评分，被 `rhythm_pattern` 依赖 |
| `web_manager/` | 节奏型模板的 Web CRUD 管理器 + 浏览器 Web Audio 试听 |

## 安装

需要 Python ≥ 3.13，使用 [`uv`](https://docs.astral.sh/uv/) 管理依赖。

```bash
uv sync
```

## 快速开始

### 选型与编排

```python
from pytheory import Fretboard
from rhythm_pattern import (
    enumerate_rhythm_patterns, arrange_progression, SelectionContext,
    fingering_sequence,
)

gtr = Fretboard.guitar()
progression = [("C", 4), ("G", 4), ("Am", 4), ("F", 4)]

# 逐和弦贪心选型（每个和弦取第 1 名）
events = enumerate_rhythm_patterns(progression, gtr, section="chorus", style="pop")
for e in events:
    print(e.chord, e.beats, e.pattern.name, e.grid.cells)

# 整段编排：Top-K 候选 + DP 选路径，输出连贯不割裂的序列，
# 并按和弦在段落中的位置（首/中/尾）自动应用位置维度（尾和弦收束处理）
ctx = SelectionContext(section="chorus", style="pop", bpm=120)
arranged = arrange_progression(progression, gtr, ctx=ctx)
for e in arranged:
    print(e.chord, e.beats, e.pattern.name)

# 栅格转指法动作序列（带显式 duration 时值）
for action in fingering_sequence(events[0].grid):
    print(action.kind, action.strings, action.duration)
```

`enumerate_rhythm_patterns` 逐和弦无状态取第 1 名，适合单点查询；`arrange_progression` 跨和弦 DP，保证整段连贯。`SelectionContext` 收敛所有「来自歌曲属性的选择因素」（段落、风格、技法基线、拍号、BPM），字段全可选，未填的维度退回默认行为，裸调用与有歌曲分析组件接入两种场景中立。

### 指法枚举

```python
from chord_fingering import enumerate_fingerings
from pytheory import Fretboard

gtr = Fretboard.guitar()
# 枚举 C 大和弦的所有指法，按可演奏性升序返回
for f in enumerate_fingerings("C", gtr, max_fret=5, max_stretch=4, limit=5):
    print(f.positions, f.tones)
# 首选即经典开放 C 指法 (None, 3, 2, 0, 1, 0)
```

非标准调弦同样工作（pytheory 内置 `Fretboard.chord(name)` 查表在非标准调弦下失效）：

```python
dad = Fretboard.guitar(tuning="dadgad")                          # DADGAD
uk  = Fretboard.ukulele()                                         # 尤克里里 GCEA
custom = Fretboard.guitar(tuning=("C4", "G3", "D3", "A2"))       # 完全自定义
```

### Web 管理器 + 试听

```bash
uv run rhythm-web                       # 或 uv run python -m web_manager.server
# 默认 127.0.0.1:8000，DB 不存在时自动从硬编码库 seed
```

浏览器里对节奏型模板做 CRUD（存 `rhythm_pattern/data/templates.json`），并直接用 Web Audio 试听——服务端只返回 JSON 音符列表（`/api/preview`），音频在浏览器合成，零服务端音频依赖。

## 核心概念

### 栅格动作自带显式时值

所有节奏型落在 **16 分音符时值**上：一拍 = 4 个 16 分位置。栅格每个动作（扫弦/拨弦/休止）自带 `duration`（占多少个 16 分位置），直接表达该动作的时值：

- `Stroke("D")` 下扫 / `Stroke("U")` 上扫（`direction` + `duration`）
- `Pluck(role=...)` 拨弦/琶音（`role` 音级角色 + 实例化后的弦号 + `duration`）
- `Rest(duration)` 休止（真静默），休止本身也是一种「音符」，有自己的时值

延续（音持续多久）由发音动作的 `duration` 表达，休止由 `Rest` 表达，职责单一、符合乐理——不再由空格位置推断。序列所有动作 `duration` 之和 = 栅格总时值（`4 × beats`），时间轴完整对齐。转谱（`fingering_sequence`）与试听（`pattern_to_notelist`）共用同一套时值语义。

### 选型打分维度（代价相加）

`pattern_cost` 消费 `SelectionContext`，把以下维度折算到同一尺度：

1. **拍数可行性**（硬约束）：`chord_beats < pattern.min_beats` 直接剔除
2. **段落契合**：当前段落不在 `pattern.sections` 里则额外罚分
3. **风格匹配**：模板风格 != 请求风格时固定罚分（不剔除，允许跨风格借用降级）
4. **技法基线**（段落级混排）：基线为 `arpeggio` 时扫弦模板罚分、`strum` 时分解模板罚分
5. **密度贴合**：模板密度与该段落 + 和弦位置的目标密度之差
6. **整动机奖励**：占满一个专属整动机时减分
7. **拍号契合**：非 4/4 拍下 4 拍周期模板罚分（仅显式给拍号时介入）
8. **BPM 可演奏性**：高 BPM 下过密模板罚分、低 BPM 下高密度连续扫弦轻微罚分
9. **扫弦可行性**：复用 `chord_fingering.count_muted`，全扫模板配不利闷弦结构时罚分
10. **进行级连贯性**：相邻和弦拍数变化时密度方向一致的模板减分

### 弦角色（对调弦中立）

分解模板用 `string_role` 表达「拨哪根弦」的**意图**，而非固定弦号：

- `Root()` / `Third()` / `Fifth()` / `Seventh()` — 按音级，可选 `region`（`bass`/`treble`/`avoid_bass`）约束音区
- `TopN(n, span)` — 顶 n 根弦，`span` 取 `comfortable`/`narrow` 控制顶底音距
- `All()` — 拨全部发音弦（「拨弦版扫弦」）

选型时按当前和弦首选 voicing 调 `role.resolve(voicing)` 实例化成具体弦号填入 `Pluck.strings`。换和弦/换调弦自动重映射，故同一模板 `53231323` 在 C 上实例化出 `5-3-2-3-1-3-2-3`、在 G 上自动换成根音更低的弦序。

### 指法枚举的两种排序模型

`enumerate_fingerings` 的 `ranking` 参数：

- **`"playable"`（默认）** — 连续代价模型（`playability.playability_cost`）。把位高度、横按、内部闷音、空弦收益、省略和弦音等折算成同一尺度上的分数，**可交换**：低把位不再无条件压过一切，能为「少一次横按」让步。手指分配可行性作为**硬约束**，按不出来的手型直接剔除。
- **`"legacy"`** — 旧的 `identify()` 硬检查 + `rank_key` 分层（字典序）排序，把位低优先于一切，不可交换。

**手指分配可行性（硬约束）**：`max_stretch` 跨度检查只能判断「手能不能张那么开」，判断不了「四根手指够不够用、绕不绕得过去」。`playability.plan_fingers` 把按弦位置真的分配给四根手指（可含食指横按与非食指小横按），分配不出来即视为弹不了——如 `(1,2,3,4,5,None)` 跨度合法但要五根手指，`(1,0,3,2,1,None)` 同品两指间夹更高品绕不过去（真弹 F 只能靠食指横按）。

**和弦音省略**：`playability.required_pitch_classes` 按吉他惯例给出可省音级——三和弦不允许任何省略；七和弦及以上允许省完全五音；音数 ≥ 5 时十一音也可省；减五、增五不在可省之列（`dim`/`aug` 的定性音）。`allow_omissions=True`（默认）时开放 C7 `x32310`（缺五音 G）才会被找到。

### Web 管理器的解耦契约

`web_manager/adapter.py` 是唯一桥接层，只导入 `rhythm_pattern` **公开**符号（`set_pattern_source`/`instantiate_pattern`/`resolve_voicing`/`TemplateRepository` 等），绝不碰 `_` 前缀私有函数。选型数据源用全局可注入源：`set_pattern_source(source)` 注入 `PatternSource`，默认仍是硬编码 `STRUM_PATTERNS`（向后兼容），web 启动时注入 `DbPatternSource(repo)` 使编辑后的模板生效。集成到别的项目时 `web_manager` 可整体丢弃而不影响 `rhythm_pattern`。

模板 `id` 只存于 DB 记录（不进 `StrumPattern` 构造，核心模型零改动），初始 `id=name`，**id 不可变**，name 可编辑但仓库强制 name 唯一。

## 项目结构

```
rhythm_pattern/           # 核心：节奏型模板库 + 选型器
  model.py                # 数据模型：Stroke/Pluck/Rest/RhythmGrid/StrumPattern/FingeringAction
  strum_patterns.py       # 模板库 STRUM_PATTERNS + 选型器 + arrange_progression + SelectionContext
  string_role.py          # 弦角色：Root/Third/Fifth/Seventh/TopN/All（按 voicing 实例化弦号）
  serialization.py        # JSON 模板仓库 TemplateRepository + 旧格式迁移工具
  data/templates.json     # 模板数据库（14 个初始模板，由硬编码库 seed）
chord_fingering/          # 基础库：和弦指法枚举与可演奏性评分
  fingering_enumerator.py # 枚举器：笛卡尔积搜索 + 物理剪枝 + 排序
  playability.py          # 可演奏性模型：手指分配 (硬约束) + 连续代价 (评分)
web_manager/              # 节奏型模板 Web CRUD + 浏览器 Web Audio 试听
  server.py               # stdlib http.server 路由
  adapter.py              # 唯一桥接层（只碰 rhythm_pattern 公开 API）+ pattern_to_notelist
  static/                 # 原生 JS 前端（无 npm）
scripts/seed_templates.py # 从硬编码库导出模板到 JSON
rhythm_main.py            # 节奏型选型/编排的审计入口（内联断言验证）
main.py                   # 指法枚举的演示 + 内联断言验证入口
tests/                    # pytest 测试
```

## 验证

```bash
uv run pytest              # 单元测试
uv run rhythm_main.py      # 节奏型选型/编排审计（内联断言）
uv run main.py             # 指法枚举验证（内联断言 + 基准集）
```

`rhythm_main.py` 每段演示都带 `assert`，断言失败即视为回归：栅格对齐（任意拍数总时值 = 4×拍数）、拍数门槛、进行级连贯性、技法基线、弦角色实例化、整段编排 DP 等。`main.py` 的核心回归是 `check_benchmark`——一组吉他教材常用指法（C / G / D / Am / F / C7 / Em7 ...）必须排进前 3 名，改 `chord_fingering/playability.py` 权重后必须重跑。

## 数据库工具

```bash
uv run python -m rhythm_pattern.serialization --seed              # 从硬编码库导出模板到 JSON
uv run python -m rhythm_pattern.serialization --migrate-legacy    # 迁移旧 None 格 DB 到新显式 duration 格式
uv run python -m rhythm_pattern.serialization --path <file>       # 指定数据库路径
```

`--migrate-legacy` 供迁移用户旧 DB 自定义模板用：旧模型 `None` 格兼表「延续/休止」，迁移后按旧 `fingering_sequence` 逻辑投影到新显式 `duration` 格式。硬编码库已直接写成新格式，不需迁移。

## 依赖

- `pytheory >= 0.57` — 音高与指板计算（含和弦解析）
- `music21 >= 10.5` — pyproject 声明但当前代码未直接引用
- `pytest >= 9.1`（dev）
