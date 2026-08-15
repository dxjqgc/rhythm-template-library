# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

吉他弹唱节奏型模板库 + 自动选型/编排，配套指法枚举器与浏览器试听 Web 管理器。三个子包：`rhythm_pattern`（核心）、`chord_fingering`（被依赖的基础库）、`web_manager`（可丢弃的 Web CRUD + 试听）。构建在 `pytheory` 之上，对任意调弦中立。

## 环境

- Python ≥ 3.10（README 标 3.13，pyproject 实际下限是 3.10），用 `uv` 管理依赖。所有命令前置 `uv run`。
- `uv sync` 安装。依赖：`pytheory`（音高/指板，核心）、`music21`（pyproject 声明但当前代码未直接引用）、`pytest`（dev）。

## 常用命令

```bash
uv run pytest                                    # 单元测试
uv run pytest tests/test_rhythm_pattern.py -k "68"   # 单个文件 / 匹配名
uv run pytest tests/test_serialization.py::TestX    # 单个测试类/函数

uv run rhythm_main.py     # 节奏型选型/编排审计（内联断言，见下）
uv run main.py            # 指法枚举验证（内联断言 + 基准集）

uv run rhythm-web         # 启 Web 管理器，默认 127.0.0.1:8000，DB 不存在自动 seed

uv run python -m rhythm_pattern.serialization --seed            # 硬编码库 → JSON
uv run python -m rhythm_pattern.serialization --migrate-legacy  # 旧 None 格 DB → 新 duration 格
```

## 验证约定（重要）

仓库有两套验证，**改完对应代码两套都要跑**：

1. **`pytest`**（`tests/`）— 单元测试。
2. **入口脚本内联断言** — `rhythm_main.py` 和 `main.py` 不是普通 demo，每段演示都带 `assert`，断言失败即回归。这是仓库**主要的回归防线**，pytest 不覆盖全部。

关键基准集（改权重后必跑）：
- `main.py::check_benchmark` — 吉他教材常用指法（C/G/D/Am/F/C7/Em7…）必须排进前 `TOP_N=3`。改 `chord_fingering/playability.py` 权重后必跑。
- `rhythm_main.py::check_benchmark` — 一组「进行+段落+风格」的公认首选模板必须排进前列。改 `rhythm_pattern/strum_patterns.py` 模板库或 `pattern_cost` 的 `W_*` 权重后必跑。

`rhythm_main.py` 还断言栅格对齐（任意拍数总时值 = `ticks_per_beat × 拍数`）、6/8 拍号契合（`check_68`）、弦角色实例化（`check_string_roles`）等。

## 架构要点

### 数据模型与时值语义（`rhythm_pattern/model.py`）

所有节奏型落在 **16 分音符时值**上，但「一拍 = 多少个 16 分位置」由拍号分母决定（`ticks_per_beat`）：`/4` 拍号一拍 = 4 tick，`/8` 拍号（6/8、3/8）一拍 = 附点 8 分 = 3 tick。栅格每个动作**自带 `duration`**（占多少 tick），直接表达时值：

- `Stroke("D"|"U")` — 扫弦，`direction` + `duration` + `accent`
- `Pluck(role=...)` — 拨弦/琶音，`role` 弦角色 + 实例化后弦号 + `duration` + `accent`
- `Rest(duration)` — 真静默休止（无 accent）

发音动作的 `accent`（`strong`/`weak`/`default`）表达复拍子（6/8）拍内强弱分组，试听映射成 velocity。**序列所有动作 `duration` 之和 = `ticks_per_beat × beats`**——这是不变量，转谱（`fingering_sequence`）与试听（`pattern_to_notelist`）共用同一套时值语义。模板自带 `time_signature`，默认 `(4,4)` 向后兼容。

### 选型打分（`rhythm_pattern/strum_patterns.py`）

两个入口：
- `enumerate_rhythm_patterns(progression, gtr, ...)` — 逐和弦贪心取第 1 名，无状态，单点查询。
- `arrange_progression(progression, gtr, ctx=..., k=...)` — 整段编排：Top-K 候选 + DP 选路径，保证连贯，并按和弦段内位置（首/中/尾）自动应用位置维度（尾和弦收束）。

`pattern_cost` 消费 `SelectionContext`（收敛段落/风格/技法基线/拍号/BPM，字段全可选，空则降级到默认 chorus/pop），把 10 个维度折算成同一尺度连续代价：拍数可行性（硬约束剔除）、段落契合、风格匹配、技法基线、密度贴合、整动机奖励、拍号契合、BPM 可演奏性、扫弦可行性（复用 `chord_fingering.count_muted`）、进行级连贯性。**权重是文件顶部 `W_*` 常量**（16 个），调权重先看这里。`W_TIME_SIG_MISMATCH` 量级大到不入候选（跨拍号借用重罚）。

### 弦角色对调弦中立（`rhythm_pattern/string_role.py`）

分解模板用 `string_role`（`Root`/`Third`/`Fifth`/`Seventh`/`TopN`/`All`）表达「拨哪根弦」的**意图**而非固定弦号。选型时按当前和弦首选 voicing 调 `role.resolve(voicing)` 实例化成具体弦号填入 `Pluck.strings`。换和弦/换调弦自动重映射（同一模板在 C 上 `5-3-2-3-1-3-2-3`、在 G 上自动换弦序）。

### 指法枚举两种排序（`chord_fingering/`）

`enumerate_fingerings` 的 `ranking` 参数：
- `"playable"`（默认）— `playability.playability_cost` 连续代价模型，可交换（低把位不再无条件压倒一切）。**手指分配可行性是硬约束**：`playability.plan_fingers` 把按弦位置真分配给四根手指（含横按），分配不出即剔除——跨度合法但四指按不出的手型（如需五指、同品两指夹更高品）会被剔除。
- `"legacy"` — 旧 `identify()` 硬检查 + `rank_key` 字典序分层，把位低优先于一切，不可交换。

`required_pitch_classes` 按吉他惯例给可省音级（三和弦不省；七和弦及以上可省完全五音；音数≥5 十一音也可省；减五/增五不省）。`allow_omissions=True`（默认）才找得到开放 C7 `x32310`（缺五音）。

### Web 管理器解耦契约（`web_manager/adapter.py`）

`adapter.py` 是**唯一桥接层**，只导入 `rhythm_pattern` **公开**符号，绝不碰 `_` 前缀私有函数——这是硬约束，保证 `web_manager` 可整体丢弃而不影响核心。集成到别的项目时 `web_manager` 可删。

**数据源注入 seam**：选型器用全局可注入源 `set_pattern_source(source)`（默认硬编码 `STRUM_PATTERNS`，向后兼容）；Web 启动时 `install_db_source(repo)` 注入 `DbPatternSource(repo)` 使编辑后的模板生效。改选型数据流走这条 seam，不要绕过。

`pattern_to_notelist` 是试听核心数据契约：服务端只返回 JSON 音符列表（`/api/preview`），音频在浏览器用 Web Audio 合成，零服务端音频依赖。

模板 `id` 只存于 DB 记录（不进 `StrumPattern` 构造，核心模型零改动），初始 `id=name`，**id 不可变**，name 可编辑但仓库强制 name 唯一。模板库存 `rhythm_pattern/data/templates.json`。

## 全局工作约束

参见用户全局 CLAUDE.md：未经用户明确说出「提交/推送/commit/push」等词，**绝不**执行 `git commit/push/tag/reset/rebase/merge` 或建分支；修完代码停在工作区未提交状态，汇报「已完成、待确认是否提交」。不确定是否要提交时**问**，不默认提交。
