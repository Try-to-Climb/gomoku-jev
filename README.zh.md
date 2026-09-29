# gomoku-jev

一个五子棋裁判，目的是**测量**下棋的那一方。棋盘、规则、判定、记分全是确定性代码，选手是可插拔的。
其中一个选手是 [jev](https://docs.typesafe.ai)（TypeSafe System One，一台决策引擎而不是聊天模型），
另一个是任意 OpenAI 兼容端点上的聊天模型。本地机器人和人类也是选手。

五子棋是探针，不是目的。每一手都连同**提问之前**引擎算好的客观事实一起记录，所以输棋可以被归因，
而不只是被记录下来：**这一手**本来有一步杀却没走，**那一手**放任对手形成了堵不住的威胁。

> **测出了什么。** jev 读棋盘几乎满分（单点 54/54，一条线的两端 10/10，计数 10/10，AND/OR 10/10），
> 套用**用文字写明**的规则也几乎满分（10/10）。它不会做的是从棋盘**推导后果**——「这条线还能不能成五」，
> 从棋盘问是 4/10，把同一个事实写成文字问是 10/10。完整结论见
> **[docs/FINDINGS.zh.md](docs/FINDINGS.zh.md)**，方法与原始证据见
> **[docs/EXPERIMENTS.zh.md](docs/EXPERIMENTS.zh.md)**。

*English: [README.md](README.md)*

## 一段话说清 jev 是什么

jev 是一台优秀的**「事实 → 判断」**引擎，不是一台**「局面 → 推演」**引擎。你放进 state 里的东西它
读得极准，你用文字写明的规则它能正确套用，后果已经标注好的选项它能合理地挑。它不会做的是自己把
后果推出来——凡是需要「拿一条规则套到**这个**局面上、往前看一步」的活儿，都得由调用方做完、当成
结论喂给它。把那条规则写进提示词不能替代这件事：四种措辞、两个请求字段，指令文本对答案的影响是
**零**；而一句关于这个局面的具体事实，把正解的概率从 0.01 推到 0.96。

不过有两个性质让它其实很好用。它的 confidence 是有信息量的而不是装饰：事实类问题饱和在
0.98–1.00，推断类问题糊在 0.3–0.8——它知道自己不知道，这个区间可以直接当门控用。它还高度可复现：
同一请求重复 5 次给出同一个错手、概率分布几乎逐位相同，所以「多试几次就对了」不是策略。实践读法：
**把它当决策层，用在你自己的确定性代码已经标注过后果的选项之上，而不是当推理层。** 照这么做之后，
它从全负变成一局不失，但全程没有形成过一次自己的威胁——那是分工的结果，不是棋力。

## 安装

```bash
pip install gomoku-jev              # 引擎、机器人、实时网页、jev 后端
pip install "gomoku-jev[openai]"    # 加上聊天模型这一席
```

从源码：`pip install -e ".[openai]"`。

每一席各需要什么：

| 想做什么 | 装什么 | 配什么 |
| --- | --- | --- |
| 本地机器人对战、自己下、看或回放仓库里已有的对局 | 什么都不用装 | 无 |
| 让 jev 下棋 | `pip install -e .` | `TYPESAFE_API_KEY` —— 需要**你自己的** TypeSafe 账号或试用额度；jev 是商业产品，本仓库不附带访问权 |
| 让聊天模型下棋 | `pip install -e ".[openai]"` | `OPENAI_API_KEY`，或只给 `OPENAI_BASE_URL`（免 key 的本地端点） |

第一行是字面意思：裸 clone 直接跑 `python -m gomoku.cli --black heuristic --white random`
和 `python -m gomoku.replay`，不会 import 任何第三方包。缺 key 时给的是一行可操作的说明，
不是 traceback。

## 不需要任何 API key 就能跑

引擎、裁判、记分和网页视图是纯标准库，所以这些命令装完立刻可用：

```bash
# 两个本地机器人打 4 局并输出记分表
gomoku --black heuristic --white random --games 4 --seed 1

# 在浏览器里看：棋盘、着法列表、每位选手的原始回复
gomoku --black heuristic --white random --size 9 --live

# 自己下一局
gomoku --black human --white heuristic
```

`--live` 会在 `127.0.0.1:8765` 起一个本地 HTTP 服务（不传 `--live-host` 就不会暴露到机器外）。
如果浏览器和服务之间有代理，用 `--live-file board.html` 写一个自包含页面，完全不需要网络。

## 让 jev 下棋

```bash
export TYPESAFE_API_KEY=...          # 或 TYPESAFE_API_KEY_FILE=/仓库之外的/路径

gomoku --black jev --white heuristic --games 2 --size 9 --out results/jev.json
python3 -m gomoku.probe              # 连通性 + 五个固定局面
```

jev 不聊天，它回答**关于某个状态的问题**。所以一手棋 = 一个 `choice` 问题：state 是局面，
options 是一份有界的合法点菜单，答案除了落子还带一个概率分布和置信度。这个分布是这个项目存在的
主要理由——它可以和引擎已知为真的事实做对照。

接口细节和「哪些公式是官方文档写的、哪些是逆推出来的」见
[docs/jev-api-notes.md](docs/jev-api-notes.md)。

## 让其他 LLM 下棋

任意 OpenAI 兼容端点都行——官方 API、OpenRouter、vLLM、llama.cpp、Ollama：

```bash
export OPENAI_API_KEY=sk-...
export OPENAI_BASE_URL=https://openrouter.ai/api/v1     # 可选
gomoku --black openai:openai/gpt-oss-120b --white jev --size 9 --live

# 本地端点完全不需要 key
OPENAI_BASE_URL=http://localhost:11434/v1 gomoku --black openai:qwen3:32b --white heuristic
```

model id 取决于你的端点怎么叫它：`openai/gpt-oss-120b` 是 OpenRouter 的命名，官方 API 要
`gpt-4o-mini`，Ollama 要 `qwen3:32b`。这个字符串是直接透传的，所以写错会拿回端点自己的报错。

两种模式。`choice`（默认）给模型**和 jev 完全相同**的候选菜单，并要求 jev 的答案形状，所以数字可比。
`free`（`openai:<model>|free`）只给棋盘、要一个坐标，这更贴近真实用法，也是唯一可能走出非法点的模式。

### 加一个自己的后端

一个文件加一次 `register` 调用。包里其他地方都不用改：CLI、`--backend` 的取值、消融工具和诊断工具
读的是同一个注册表。

```python
from gomoku.backends import Backend, register
from gomoku.players import MoveResponse, Player

class MyPlayer(Player):
    def propose(self, view, feedback=None):
        # view.board_text(), view.stone_lists(), view.legal_moves(), ...
        return MoveResponse(move=view.legal_moves()[0])

register(Backend("mine", "my engine", lambda arg, seed, overrides: MyPlayer()))
```

之后 `gomoku --black mine --white heuristic` 就能用，`gomoku --list-backends` 里也会出现。

## 测什么

每位选手、整个系列赛累计：

| 指标 | 含义 |
| --- | --- |
| `win_conversion` | 有一步成五的机会时有没有走（最难辩解的一项，几乎不需要棋力） |
| `block_rate` | 对手**只有一个**成五点时有没有堵上（两个点记为 `unavoidable_loss`，不算失误） |
| `of.make` / `of.stop` | 早一层：做出/拦住一个有**两个**成五点的四——那是堵不住的 |
| `illegal_move_rate`、`parse_failure_rate` | 格式与规则遵循，分母是尝试次数而不是手数 |
| `forfeits`、`referee_fallbacks` | 彻底给不出可用答案的次数 |

对局之间自动换边，先手优势相互抵消。指标的精确定义见 [docs/DESIGN.md](docs/DESIGN.md)。

## 提示词版本

发给模型的每一个字都在 [`gomoku/templates/`](gomoku/templates/) 里，代码不硬编码任何提示词。
提示词分四段：规则、**战术**（实验轴）、战场状态、少量各家 API 特有的粘合。查找规则是
「后端优先、回落共享」，并且有一条单测会在后端文件静默遮蔽共享文件时失败——因为那一刻两位选手就
不再是用同一把尺子量的了。

```bash
gomoku --list-prompts                                  # 有哪些版本
gomoku --black jev@p0-one-move --white jev@p1-threat-ladder --size 9
python3 -m gomoku.ablate --backend jev --repeats 5     # 每种措辞的命中率
```

战术文件名**就是**版本号，会记进每条对局记录，所以不同提示词下的数据永远不会被平均在一起。
要写自己的版本，往 `gomoku/templates/shared/tactics/` 丢一个文件，或者用 `--templates` 指向
仓库之外的目录。

## 工具

| 命令 | 作用 |
| --- | --- |
| `gomoku`（`python -m gomoku`） | 打对局、记分、写 JSON |
| `python -m gomoku.probe` | 连通性 + 五个有已知答案的固定局面 |
| `python -m gomoku.audit` | 打一局并写一份给人看的 Markdown，每手 flush |
| `python -m gomoku.ablate` | 固定局面上的提示词/事实消融，重复采样 |
| `python -m gomoku.perceive` | 问后端**看见了什么**，而不只是它走了什么（fan-out） |
| `python -m gomoku.vision` | 把一个判断拆成原子步骤分别问 |
| `python -m gomoku.infer` | 完全不看棋盘的纯规则算术，作为对照 |
| `python -m gomoku.postmortem` | 把某一手在各种条件下重新问一遍 |
| `python -m gomoku.replay` | 把存下来的对局变成一个可逐手回放的 HTML 页面 |

实验产生的记录在 [`results/`](results/)，按实验编号索引在
[`results/README.md`](results/README.md)。看任意一局：

```bash
python3 -m gomoku.replay results/audit-optionfacts-threat.json --open
```

它写出一个自包含页面：带手数滑块的棋盘，以及每一手的模型原始回复、置信度、token 用量、
拿到的候选菜单、和引擎当时已经算好的事实。不需要服务，不需要网络。

## 结构

```
gomoku/board.py rules.py game.py     几何、唯一的判定函数、行棋顺序
gomoku/analysis.py                   威胁、死线、记分的客观依据
gomoku/candidates.py                 所有 choice 后端共用的有界候选菜单
gomoku/match.py metrics.py           裁判（保留被拒的尝试）与记分
gomoku/backends.py                   注册表：名字 -> 选手
gomoku/jev_client.py llm_player.py   jev 传输层，以及 jev 作为选手
gomoku/openai_player.py              任意 OpenAI 兼容聊天模型作为选手
gomoku/templates/                    全部提示词，以文本形式
gomoku/live.py live.html             浏览器视图，实时或从存档
gomoku/replay.py                     存档对局 -> 自包含页面
docs/                                结论、实验报告、设计说明、jev API 笔记
```

## 测试

```bash
python -m unittest discover -s gomoku/tests -t .
```

253 个测试，几秒钟，不联网：导入测试包会把 HTTP 层换成一个抛异常的实现，所以单测不可能悄悄烧
token。联网检查是单独的显式命令（`gomoku.probe` 那一批）。

## 范围与诚实声明

- 结论描述的是 **jev-1.13.0**，2026 年 9 月，通过这个 harness 和这些提示词测得。未经 TypeSafe 审阅。
- 对照模型是 `openai/gpt-oss-120b`。它是用来回答「这个弱点是不是 jev 特有」的参照点，不是排行榜。
- 任何开启了 `--facts` 或 `--option-facts` 的对局，被测的命题已经不是「它会不会下五子棋」，
  而是「把分析结果交给它之后它能不能做对决策」。这类对局在每条记录里都有标记，从不与其他数据合并统计。
  jev 唯一一局不败来自这个模式，那是**分工胜利而不是棋力胜利**——见
  [docs/FINDINGS.zh.md](docs/FINDINGS.zh.md) 末尾。

欢迎纠正和提交相反的实验结果；固定局面和消融工具就是为了让分歧可以用一条命令解决。

## 许可

MIT，见 [LICENSE](LICENSE)。
