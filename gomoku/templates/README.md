# 提示词模板

发给模型的每一个字都在这里，Python 代码不硬编码任何提示词文本。改提示词 = 改文件。

提示词分四段：

| 段 | 是什么 | 在哪 | 共享性 |
| --- | --- | --- | --- |
| 1 规则 | 五子棋怎么下、坐标怎么写 | `shared/rules.txt` | 共享 |
| 2 战术 | 威胁术语和优先级 —— **实验轴** | `shared/tactics/<版本>.txt` | 默认共享，可按模型覆盖 |
| 3 战场状态 | 轮到谁、棋盘图、双方棋子列表、棋谱 | `shared/state.txt` | 内容必须一致，**格式**可按模型覆盖 |
| 4 后端粘合 | 各家 API 特有的东西：怎么摆放、答案格式 | `<backend>/*.txt` | 天然独立，尽量少 |

## 查找规则

`<backend>/<文件名>` 存在就用它，否则回落到 `shared/<文件名>`。

所以想给某个模型单独换第 2 或第 3 段，**不用改代码**——把同名文件丢进那个模型的目录即可：

```bash
# 只给 jev 换一套战场状态格式（比如行号自上而下）
cp shared/state.txt jev/state.txt && $EDITOR jev/state.txt

# 只给 jev 换战术
mkdir -p jev/tactics && cp shared/tactics/p1-threat-ladder.txt jev/tactics/p1-jev-only.txt
```

默认没有任何覆盖。有一条单测（`test_no_backend_silently_shadows_a_shared_part`）会在
任何后端目录里出现与 `shared/` 同名的文件时**直接失败**——因为那一刻 jev 和聊天模型的对比就
不再是同一把尺子了。真要这么做，把它加进测试里的 `ALLOWED_OVERRIDES`，并在结论里说明。

## 文件清单

```
shared/rules.txt                第 1 段
shared/tactics/p0-one-move.txt   第 2 段：对照组（只看一步）
shared/tactics/p1-threat-ladder.txt  第 2 段：当前默认（活三/活四 + 防守）
shared/state.txt                第 3 段
shared/facts.txt                第 3 段的可选附加：引擎算好的线段事实（none/span/status/geometry/threats）
shared/options.txt              候选点文案，可附加「此手之后对手能否造活四」（--option-facts open_four）
jev/instructions.txt            第 4 段：jev choice 问题的 instructions
openai/system.txt               第 4 段：gpt-oss choice 模式 system 的骨架
openai/answer_full.txt          第 4 段：填进 system —— 要 choice + 概率分布 + 置信度
openai/answer_terse.txt         第 4 段：填进 system —— 只要 choice、禁止逐步推理（截断重试）
openai/user.txt                 第 4 段：gpt-oss 的 user（状态 + 候选清单）
openai/system_free.txt          第 4 段：free 模式（无候选菜单，只要裸坐标）的 system
```

jev 的 `state` 字段 = 第 1 段 + 第 3 段，在代码里拼；free 模式的 user 直接就是第 3 段。
这两处不需要单独的文件。

## 占位符

| 文件 | 可用占位符 |
| --- | --- |
| `shared/rules.txt` | `$rules` `$size` `$first_col` `$last_col` `$example` `$centre` |
| `shared/tactics/*.txt` | `$n` 成几个算赢 · `$n1` = n-1 · `$n2` = n-2 |
| `shared/state.txt` | `$move_no` `$colour` `$symbol` `$opponent` `$opponent_symbol` `$board` `$stones` `$history` `$facts` `$extra` |
| `shared/facts.txt` | 事实注入块的措辞，`[section]` 分段（见下） |
| `shared/options.txt` | 候选点文案，`[section]` 分段，占位符 `$col` `$row`（部分段另有 `$n` `$points` `$ways` `$best`） |
| `jev/instructions.txt` | `$colour` `$symbol` `$tactics` |
| `openai/system.txt` | `$colour` `$symbol` `$rules_block` `$tactics_block` `$answer` |
| `openai/user.txt` | `$state` `$options` |
| `openai/system_free.txt` | `$colour` `$symbol` `$rules_block` `$tactics_block` |
| `openai/answer_full.txt`、`openai/answer_terse.txt` | 无 |

语法是 `string.Template` 的 `$name`（**不是** `{name}`），这样提示词里的 JSON 示例不用转义
大括号。写错名字会直接报错并指出文件名。`$name` 后紧跟字母数字时写 `${name}`；字面量 `$`
写成 `$$`；文件末尾最后一个换行会被去掉。

## 常用命令

```bash
# 换一套战术并做对照
cp shared/tactics/p1-threat-ladder.txt shared/tactics/p2-my-idea.txt
python3 -m gomoku.ablate --backend openai --versions p1-threat-ladder,p2-my-idea --repeats 3
python3 -m gomoku.cli --black jev --white openai --prompt-version p2-my-idea --size 9

# 整套模板放到仓库外
cp -r gomoku/templates ~/my_prompts
python3 -m gomoku.probe --backend openai --templates ~/my_prompts   # 也认 GOMOKU_TEMPLATES
```

## 事实注入（`shared/facts.txt`）

第 3 段可以附加一块由引擎算好的事实，用 `--facts geometry|status|threats` 打开（默认 `none`）。
这个文件按 `[段名]` 分段，每段是一个可独立编辑的措辞模板：

| 段 | 用途 | 占位符 |
| --- | --- | --- |
| `block` | 整块的抬头 | `$lines` |
| `threats` | 威胁小节的抬头 | `$threats` |
| `row_geometry` | geometry 档的每行 | `$colour` `$coords` `$orientation` `$span` `$ends` |
| `row_status` | status/threats 档的每行 | `$colour` `$coords` `$orientation` `$status` `$reason` |
| `status_live` / `status_dead` | 死活标签 | 无 |
| `reason_live` / `reason_dead` | 死活理由 | `$span` `$n` `$ends` |
| `threat_five` / `threat_open_four` | 威胁行 | `$colour` `$points` `$n` |
| `threat_none` / `no_lines` | 兜底行 | 无 |

**硬规则：只写事实，不排序候选、不推荐落点、不出现「最佳/应该」。** 一旦文案里出现指向某一点的
建议，测的就不再是模型而是这个文件。注入档位会记进对局记录的 `facts` 字段，注入与未注入的数据
不能合并统计。

## 候选点标注（`shared/options.txt`）

`--option-facts` 把每个候选点的**后果**写进 jev 的 `criteria`，由引擎逐点模拟得出，是关于该选项的
事实、不是推荐——可能有多个点都安全，文案里不出现任何排序。可选档位：

| 档位 | 段名 | 每个选项说什么 |
| --- | --- | --- |
| `threat`（**推荐**） | `threat_five` / `threat_open_four` / `threat_none` | 最紧急的那层威胁：对手能否立刻成五 → 能否造活四 → 都不能 |
| `open_four` | `opponent_open_four_yes` / `_no` | 只看活四层。**对手已有冲四时会整体失声**（每个选项都答「造不出活四」），见 EXPERIMENTS.md E15 |
| `ways` | `ways` | 对手还剩几条可成五的线（连续量，一句话一个数字） |
| `windows` | `windows` | 同上，但句子更复杂——实测被忽略，保留作反面对照 |

注意这在防守局面里是**很强的提示**（往往只有堵点是安全的，等于把答案标出来）。它的实验价值在于
验证 `criteria` 文字到底会不会被读——在此之前所有注入都放在 `state` 里。结论见 EXPERIMENTS.md 的
E13：会被读，而且有效。
