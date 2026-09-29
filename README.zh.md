# gomoku —— 用五子棋测 LLM

棋盘、规则、裁判、记分都在这里，选手是可插拔的。LLM 走棋只是 `Player` 的一个实现：
裁判把局面渲染成文本 → 选手返回一个坐标 → 裁判判合法性与胜负 → 全过程写进 JSON。

本次交付：引擎 + 规则 + 裁判 + 基线对手 + 记分，两个模型后端 —— **jev**（已连通）和
**任意 OpenAI 兼容端点上的聊天模型**（实验中用的是 `openai/gpt-oss-120b`）。

## 快速开始

```bash
# 基线机器人对随机走子，打 4 局并输出记分表
python3 -m gomoku.cli --black heuristic --white random --games 4 --seed 1

# jev 执黑对启发式机器人
python3 -m gomoku.cli --black jev --white heuristic --games 2 --size 9 --out results/jev_vs_bot.json

# gpt-oss 模型执黑（默认 openai/gpt-oss-120b）
python3 -m gomoku.cli --black openai --white heuristic --games 2 --size 9

# 两个后端直接对轰（自动换边）
python3 -m gomoku.cli --black jev --white openai:Qwen/Qwen3-32B --games 2 --size 9

# live 模式：开局即起一个本地网页，实时看棋盘 + 模型原始回复
python3 -m gomoku.cli --black jev --white openai --size 9 --live
python3 -m gomoku.audit --black jev --white openai --size 9 --live   # 审计文档 + 实时界面

# 一局一份逐手审计文档（Markdown，边打边写，可实时跟读）
python3 -m gomoku.audit --black jev --white openai --size 9 --seed 101

# 后端连通性 + 五个固定局面探针（含两个威胁级用例）
python3 -m gomoku.probe                      # jev
python3 -m gomoku.probe --backend openai     # gpt-oss

# 提示词消融（对照组用 --prompt-version p0-one-move）
python3 -m gomoku.ablate --backend openai --repeats 3

# 看每一步的棋盘，并把完整对局记录存成 JSON
python3 -m gomoku.cli --games 1 --render --out results/demo.json

# 只看规则文本（也就是发给模型的那段）
python3 -m gomoku.cli --show-rules

# 自己下一局
python3 -m gomoku.cli --black human --white heuristic

# 测试（126 个，全离线，约 3 秒）
python3 -m unittest discover -s gomoku/tests -t .
```

选手 spec：`random` / `heuristic` / `human` / `jev[:<model>]` / `openai[:<model>][|free]`。
`--candidates near|all` 和 `--max-candidates N` 控制给模型的候选集（见「候选集」）。

## 规则

默认是 **无禁手（free-style）五子棋**，所有维度都能在 `RuleSet` 里调：

| 项目 | 默认 | 说明 |
| --- | --- | --- |
| `size` | 15 | 棋盘边长，5–26（列字母 A–Z） |
| `win_length` | 5 | 连成几个算赢 |
| `overline` | `win` | 长连（超过 5 个）怎么算：`win` 也赢（无禁手）/ `ignore` 只有恰好 5 才赢，长连无效（标准规则）/ `forbidden` 长连是禁手，走出来立刻判负（连珠规则，默认只限制黑方；白方长连仍然算赢） |
| `opening` | `free` | `center_first` 黑棋第一手必须走天元；`pro` 黑一天元、黑二必须离天元至少 3 路 |
| `max_retries` | 2 | 一步之内允许模型重答几次（答案无法解析或落子非法时，裁判会把原因反馈回去） |
| `illegal_move` | `forfeit` | 重试用尽后：`forfeit` 直接判负 / `random_fallback` 裁判代其随机落子（对局继续，事后统计里记一笔） |
| `max_plies` | `None` | 手数上限，到了判和（给慢模型兜底） |

判定要点：

- 黑先白后，交替落子，落子后不可移动、不可提子。
- 横、竖、两条斜线四个方向上任一方向达成连子即胜。中间有空格不算。
- 棋盘填满且无人成五 → 和棋（`board_full`）。
- 非法落子的原因码是稳定字符串：`occupied`、`out_of_bounds`、`opening_center_required`、
  `opening_pro_distance`、`game_over`，统计和日志里直接用。

## 坐标

`H8` 式记法：列是从左到右的字母 `A`…（**不跳过 I**），行是从下到上的数字 `1`…`15`，
15×15 的天元是 `H8`。内部坐标是 0 起的 `(row, col)`，`row=0` 在最上面，两者靠
`gomoku.notation` 互转。

`parse_move()` 对模型输出是宽容的（能不能好好遵守格式，用统计量去衡量，而不是直接判死）：

- 裸坐标 `H8` / `h8.` / `我走 H8`（取最后一个坐标，因为模型习惯先罗列再给结论）
- JSON：`{"move": "H8"}`、`{"row": 8, "col": 8}`、`{"move": [8, "H"]}`
- 自然语言：`row 8, column 8`、`(8, 8)`
- 会先剥掉 ` ```json ` 代码块和 `<think>…</think>`，并做 NFKC 归一（全角 `Ｈ８` 也认）

## 接 jev

jev 不是聊天模型，是「给定 state 回答 question」的决策引擎（`POST https://api.typesafe.ai/v1/systemone`，
与 `1shot.py` / `bench/run_jev.py` 同一套契约）。所以一步棋 = 一个 `choice` 问题：

- `state`：规则 + 坐标说明 + 棋盘图 + 双方棋子列表 + 棋谱（重试时附上裁判的反馈）
- `questions`：`{"move": {"type": "choice", "instructions": ..., "criteria": {"H8": "...", ...}}}`
- 返回 `{"choice": "I8", "probabilities": {...}, "confidence": 0.85}` —— 除了落子，还白拿一个
  候选点概率分布和置信度，正好可以和 `analysis` 里算出的客观事实做校准对比

候选点（`criteria`）必须有界，规则见下面「候选集」一节。只剩一个合法点时直接落子，不调 API。

API key 从 `TYPESAFE_API_KEY` 读，没有就回退到仓库里的 `apikei.txt`，全程不打印（只打印
`len=... ...后四位`）。

```bash
# 连通性 + 三个固定局面（空盘、必须堵、能一步赢）
python3 -m gomoku.probe

# 再加一局 9x9 对启发式机器人的实战
python3 -m gomoku.probe --game 9 --max-plies 60

# 正式对局（jev 执黑）
python3 -m gomoku.cli --black jev --white heuristic --games 2 --size 9 --out results/jev_vs_bot.json
```

已跑通的结果（`results/jev_probe.json`，jev-1.13.0）：

| 探针 | 结果 |
| --- | --- |
| ping | HTTP 200，1.3 s |
| 空盘首手 | H13，confidence 0.50（无对错，只验请求形状） |
| 必须堵 | I8 ✓，confidence 0.85（概率分布里 I8 占 0.87） |
| 能一步赢 | H9 ✓，confidence 0.61（另一个杀点 C9 拿 0.22） |
| 9×9 实战 | 12 手负于启发式机器人（对方冲四双活口，属 `unavoidable_loss`） |

每步约 0.5 s、约 1.6k input tokens。`llm_player.py` 里所有网络失败都会变成
`MoveResponse.error`，由裁判按 `max_retries` / `illegal_move` 策略处理并记进日志，
不会中断对局。

## 结构

```
board.py      棋盘几何：落子/撤销、某点某方向的连子长度与活口数
rules.py      RuleSet + judge_placement（唯一的胜负判定入口）+ describe（发给模型的规则文本）
game.py       Game（走子、合法性、终局）与 GameView（选手只读视图）
notation.py   坐标记法 + 宽容解析器
render.py     棋盘/棋子列表/棋谱的文本渲染
analysis.py   一步杀点、对手一步杀点、每步标注（该赢没赢/该堵没堵）+ 启发式打分表
candidates.py 候选点菜单（near/all、截断、战术点保底、打乱）+ choice 答案解析
players.py    Player 接口、RandomPlayer、HeuristicPlayer、ScriptedPlayer
templates/    ★ 全部提示词文本：shared/{rules,state,tactics/*} + <backend>/*（四段结构）
prompts.py    模板加载与渲染（含 --templates / GOMOKU_TEMPLATES 覆盖）
match.py      裁判：play_match / play_series，记录每一次尝试（含被拒的）
metrics.py    汇总：胜率、非法率、解析失败率、一步杀转化率、该堵堵住率、延迟
jev_client.py jev HTTP 客户端（key 解析、重试、usage/延迟）
llm_player.py JevPlayer：局面 -> choice 问题 -> 落子，附概率分布与置信度
openai_player.py OpenAIPlayer：choice / free 两种模式，任意 OpenAI 兼容端点
probe.py      两个后端共用的连通性与行为探针
ablate.py     提示词消融：固定局面 × 版本 × 重复次数 → 命中率
live.py       live 模式的后台 HTTP 服务（state.json + 页面）
live.html     live 页面（零依赖，轮询刷新）
factsheet.py  把引擎分析渲染成注入用的事实文本（none/span/status/geometry/threats）
audit.py      逐手审计文档生成器（Markdown + JSON）
cli.py        命令行
```

## 给 LLM 打分的指标

对局记录里每一步都带 `analysis`，是**在模型回答之前**算出来的客观事实，所以能区分
“输了” 和 “输在哪”：

- `win_conversion`：自己有一步杀时，有没有真的走那一步（最硬的指标，几乎不需要棋力）
- `block_rate`：对手只有一个成五点时，有没有堵上（对手有两个点时记为 `unavoidable_loss`，不算失误）
- `illegal_move_rate` / `parse_failure_rate`：格式与规则遵循能力（分母是尝试次数）
- `forfeits` / `referee_fallbacks`：彻底答不出来的次数
- `win_rate` / `score`、`avg_latency_s`、`attempts_per_move`

因为 `play_series` 默认换边，先手优势在两局之间互相抵消。

## 接 gpt-oss（另一位骑手）

`openai_player.py` 把 gpt-oss 上的聊天模型包成同一个 `Player`。默认模型
`openai/gpt-oss-120b`，可选 `Qwen/Qwen3-32B`、`Qwen/Qwen2.5-32B-Instruct`、
`Microsoft/phi-4`（沿用 `bench/cases.py` 里验证过的列表）。两种模式：

| 模式 | 给模型什么 | 要求回什么 | 用途 |
| --- | --- | --- | --- |
| `choice`（默认） | 和 jev 完全相同的局面文本 + 同一份候选菜单 | jev 的答案形状：`{"choice": "I8", "probabilities": {...}, "confidence": 0.8}` | 同一把尺子和 jev 对比，还能拿到概率做校准 |
| `free`（`openai:<model>\|free`） | 只有棋盘，无候选菜单 | 一个裸坐标（`H8` 或 `MOVE: H8`） | 贴近真实用法，也是唯一可能走出非法点的模式 |

`choice` 模式解析顺序：先从回复里抓 JSON（会剥掉 ` ``` ` 和 `<think>`），失败再退回
`notation.parse_move` 抓裸坐标；选了菜单外的点一律拒绝并让裁判反馈重试。
`require_distribution=False` 可以只要 `choice` 不要分布，省 output token。

鉴权：`OPENAI_API_KEY` 走托管 API；只设 `OPENAI_BASE_URL` 则走本地自建端点（Ollama /
vLLM / llama.cpp 这类不需要 key）。两个都没有时 `ensure_auth()` 在建客户端前就快速失败，
不会在对局中间才报错：

```bash
# 托管 API（OpenAI / OpenRouter / 任何兼容端点）
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://openrouter.ai/api/v1   # 可选，默认官方端点
python3 -m gomoku.probe --backend openai --game 9

# 本地端点，无需 key
export OPENAI_BASE_URL=http://localhost:11434/v1
python3 -m gomoku.probe --backend openai --model qwen3:32b
```

单测全部用假客户端（`FakeChat`），不碰 SDK、不联网。

reasoning 模型的 token 预算是个坑：`max_tokens=4096` 时 gpt-oss-120b 会把预算全烧在隐藏的
思考 token 上，返回 `finish_reason="length"` + **空 content**，表现为 38% 的「解析失败」。
默认改成 `16384`（实测一手约 3.4k output token），并加了自愈：一旦回复被截断且拿不到落子，
自动用「不要逐步推理、直接输出 JSON」的精简提示重试一次（`retry_on_truncation`，记在
`meta.truncation_retry` 里）。修完之后 4 局 0 解析失败、0 非法落子。

## live 模式

`--live` 会在对局开始时起一个后台 HTTP 服务（默认 `http://127.0.0.1:8765/`，`--live-port` 可改，
`--no-open` 不自动开浏览器），页面每 0.6 秒轮询一次 `state.json`。纯标准库，不装任何东西，
对局在主线程照常跑——浏览器关掉也不影响棋局。

页面上能看到：

- **棋盘**：CSS 画的格子，最后一手高亮，带坐标轴
- **着法列表**：每手的落子、耗时、被拒次数，以及引擎判定的标签（`成五获胜` / `该堵没堵` /
  `错过活四` / `放过活四` / `已无解` …）
- **原始回复**：点任意一手，下方显示该手**每一次尝试**的完整原文（JSON 或裸文本）、
  confidence、概率最高的几个候选、token 用量、`finish_reason`、候选菜单
- **引擎事实**：该手提问**之前**算好的一步杀点 / 可造活四点，用来对照模型的选择
- **指标**：对局结束后显示 winconv / block% / of.make / of.stop / 平均延迟

提示词不会送到前端（太长，且已经在 `.json` 日志里），只展示回复。

```bash
python3 -m gomoku.cli --black jev --white openai --size 9 --live
python3 -m gomoku.audit --black jev@p2-defence-first --white openai --size 9 --live --facts status
```

## 逐手审计

`audit.py` 打一局并写一份给人看的 Markdown，每手按固定顺序记：该手之前的局面 → **提问之前**
就算好的客观事实（自己/对手的一步成五点、合法点数）→ 给模型的候选菜单 → 模型原始回复
（含 confidence、概率分布、token、耗时）→ 落子后的局面 → 裁判判定。被拒的尝试也保留，
文末附双方第一手的**完整请求原文**。文件每手 flush，可以边打边读。

```bash
python3 -m gomoku.audit --black jev --white openai --size 9 --seed 101
# → results/audit-<时间戳>.md 和同名 .json（.json 里每手都带完整提示词）
```

样例：`results/audit-jev-vs-gptoss.md`（14 手，gpt-oss-120b 执白胜）。

## 提示词版本与消融

**所有提示词文本都在 `gomoku/templates/` 里，代码不硬编码任何一个字**（详见该目录的 README）。
提示词分四段，落在四组文件上：**① 规则** `shared/rules.txt`、**② 战术** `shared/tactics/<版本>.txt`（实验轴）、**③ 战场状态** `shared/state.txt`、**④ 后端粘合** `<backend>/*.txt`（各家 API 特有的摆放方式和答案格式，尽量少）。
查找规则是「后端优先、回落共享」：想给某个模型单独换第 2 或第 3 段，把同名文件丢进
`jev/` 或 `openai/` 即可，不用改代码。默认没有任何覆盖，且有单测会在出现同名覆盖时直接
失败——那一刻两边就不再是同一把尺子了。占位符用 `string.Template` 的 `$name` 语法（这样
提示词里的 JSON 示例不用转义大括号），写错占位符会报错并指出文件名。战术文件名即版本号，会被记进每条对局记录的 `prompt_version`，避免不同提示词的数据被平均在一起。

```bash
cp gomoku/templates/shared/tactics/p1-threat-ladder.txt gomoku/templates/shared/tactics/p2-my-idea.txt
# 改完直接用，不需要改代码
python3 -m gomoku.cli --black jev --white openai --prompt-version p2-my-idea --size 9
python3 -m gomoku.ablate --backend openai --versions p1-threat-ladder,p2-my-idea --repeats 3

# 整套模板放到仓库外（每个入口都支持 --templates，也认 GOMOKU_TEMPLATES 环境变量）
cp -r gomoku/templates ~/my_prompts
python3 -m gomoku.probe --backend openai --templates ~/my_prompts
```

| 版本 | 内容 | 问题 |
| --- | --- | --- |
| `p0-one-move` | 只有「成五」和「堵下一手的五」两级，然后一句含糊的「发展自己、压制对手」 | 视野被写死成一步，全文不出现活四/冲四/活三，也没说活四堵不住 |
| `p1-threat-ladder`（默认） | 定义四/活四/活三，六级优先级（成五 → 堵冲四 → 造活四 → 防活四 → 造活三 → 其余），外加一段「防守和进攻同等重要」：先看对手最长的线和它的活口，并明确「已经被封死的线一文不值」 | — |

用固定局面跑消融（`ablate.py`，每格重复多次取命中率，因为两个后端在相同请求下都会抖动）：

```bash
python3 -m gomoku.ablate --backend jev --repeats 5
python3 -m gomoku.ablate --backend openai --repeats 3 --only make_open_four,stop_open_four
```

| 局面 | jev P0 | jev P1 | gpt-oss-120b P0 | gpt-oss-120b P1 |
| --- | --- | --- | --- | --- |
| `must_block`（对手冲四，必堵） | 5/5 | 5/5 | ✓ | ✓ |
| `must_win`（自己能成五） | 5/5 | 5/5 | ✓ | ✓ |
| `make_open_four`（能造活四 = 必胜） | 0/5 | 0/5 | 2/3 | **3/3** |
| `stop_open_four`（必须破对手活三） | 0/5 | 0/5 | 2/3 | **3/3** |

结论分两半。对聊天模型，P1 有效：两个威胁级用例从 4/6 到 6/6，confidence 也从 0.21–0.28 升到
0.28–0.48。对 jev，**完全无效**：5 次重复每次都是同一个错手（`make_open_four` 选 B5、
`stop_open_four` 选 C5），confidence 高达 0.6–0.85。两个错手都落在第 5 行——而那一行两端已被
D5/I5 封死，根本不可能成五。也就是说 jev 的瓶颈不在指令措辞，而在**认不出死线**：它按「哪条线
子多」来选点，没有把封堵算进去。下一步对 jev 应该验证的是感知而不是指令（用 fan-out 单独问
「这条线还能成五吗」），或者在 `criteria` 里注入客观事实（P2）。

`make_open_four` / `stop_open_four` 这两个用例直接取自实战里双方各自走错的那两个局面，已经
固化进 `probe.py`，以后每次改提示词都会自动回归。

## 实验结果

两份文档：

- **[FINDINGS.zh.md](FINDINGS.zh.md)** —— jev 的能力画像（能做什么、不能做什么、怎么用它、与
  gpt-oss 的对比）。想知道结论看这份。
- **[EXPERIMENTS.zh.md](EXPERIMENTS.zh.md)** —— 实验过程报告（16 组实验、方法、关键原始回复、
  方法论教训）。想复现或质疑结论看这份。

一句话结论：

> gpt-oss-120b **8:0** 胜 jev。差距集中在一项可定位的单一能力——把棋盘上读到的事实代入规则、
> 得出「将来可不可能」的结论。两个模型在读棋盘事实（各 124/124）和纯文字规则推理（各 10/10）
> 上完全相同，差异 100% 在中间这一步（jev 19/40，gpt-oss 40/40）。

## 候选集（choice 模式共用）

`candidates.py` 只做一件事：把局面变成一份有界的合法点菜单。

```
1. legal   = 当前所有合法空点
2. pool    = "near" → 已有棋子周围 Chebyshev 半径 radius(默认2) 内的空点
             "all"  → 全部 legal（空盘时 near 自动退化成 all）
3. 超过 max_candidates（默认 30）→ 按启发式 attack_score(我)+attack_score(对手) 降序截断
4. include_tactical（默认开）→ 把双方的一步成五点无条件补回来
5. shuffle（默认开）→ 打乱顺序
```

实测（15×15「必须堵 I8」局面，jev-1.13.0）：

| 候选策略 | 选项数 | 结果 | confidence | input tok | output tok |
| --- | --- | --- | --- | --- | --- |
| `near` / 30 | 30 | I8 ✓ | 0.85 | 1626 | 267 |
| `all` / 225 | 218 | I8 ✓ | 0.72 | 5736 | 1852 |

给全部空点可行，代价是 input ×3.5、output ×7。默认用 30 个是省 token 的折中，代价是
第 3 步的启发式决定了模型能看到哪些非战术点 —— 这是唯一残留的测量污染源，用
`--candidates all` 可以完全消除（9×9 只有 81 个点，完全跑得起）。第 4、5 步保证了
「该赢没赢 / 该堵没堵」这两个硬指标不受候选集影响。

## 下一步

- 补活三/活四层级的威胁标注：现有指标只看「一步成五」，看不到 jev 真正输掉的那个窗口
- 针对 jev 的死线盲区：先用 fan-out 单独测「这条线还能不能成五」的感知，再决定要不要上 P2 事实注入
- 多打几局、多接几个模型（`Qwen/Qwen3-32B` 等）把 4:0 这个样本量做厚
- 把 `confidence` 和「这步是否客观正确」对起来做校准曲线（`bench/confidence_vs_drift.py` 的思路）
- 消融 `max_candidates` / `shuffle` / `include_tactical`，以及 `choice` vs `free` 模式
