# regex-risk-validator

一个用于代码评审场景的 Python 正则表达式风险验证库：在**有限预算**内，把待审正则的编译与匹配放进独立子进程执行，收集"是否可能因回溯长时间占用 CPU"的证据。

> **重要定位说明**：本库只能提供有限预算内的风险**证据**，不能判定任意正则的复杂度，也不能证明某个正则不存在 ReDoS 风险。"没有找到慢样例"不等于"正则安全"。

- 仅使用 Python 3.11+ 标准库，无第三方依赖，不需要 Docker。
- 不使用 shell 执行命令，不使用 `eval`/`exec`，不使用 pickle。
- 实际验证环境：Linux（WSL Ubuntu，Python 3.14）。Windows（spawn 启动方式）按设计兼容，见下文"平台支持"。

## 快速开始

```python
from regex_risk import analyze, Limits, CandidateRule

# 最简单：验证调用方给出的字符串
report = analyze(r"(a+)+$", string="a" * 28 + "b",
                 limits=Limits(per_match_timeout_seconds=1.0))
print(report.assessment.summary)

# 完整形式：flags、匹配方式、候选生成规则
report = analyze(
    r"(\w+,)+$",
    flags=0,
    mode="search",            # "search" | "match" | "fullmatch"，报告中保留实际使用的方式
    string="x,y,z!",
    rules=[CandidateRule(fragment="ab,", suffix="!",
                         lengths=[1_000, 2_000, 4_000, 8_000])],
    limits=Limits(total_time_seconds=15.0),
)

from regex_risk import save_report, replay_report
save_report(report, "report.json")
fresh = replay_report("report.json")   # 重新执行验证，返回全新结果
```

## API 概览

- `analyze(pattern, flags=0, mode="search", string=None, rules=None, limits=None) -> AnalysisReport`
  一次性分析：先验证 `string`，再按规则（或内置启发式）生成逐渐增长的候选输入。
- `RegexRiskAnalyzer(limits)` 可复用实例；`evaluate_one(...)` 单条评估；超时后实例可继续处理正常任务。
- `CandidateRule(fragment, prefix="", suffix="", lengths=[...])` 候选构造规则：按目标总长度重复 `fragment` 并包上前/后缀。不提供 `rules` 时使用有限的启发式策略（从模式中提取被量词包裹的字面片段，附加触发回溯的尾字符），该启发式只用于**生成输入**，不作为任何安全性结论的依据。
- `save_report(report, path)` / `load_report(path)` / `replay_report(path_or_report)`。

### 可区分的结果类别（`Observation.outcome`）

| 值 | 含义 |
|---|---|
| `match` / `no_match` | 正常匹配 / 正常不匹配 |
| `invalid_pattern` | 表达式无法编译 |
| `timeout` | 到达期限，子进程已被终止并回收 |
| `worker_error` | 子进程异常退出、无输出或输出无法解析 |
| `launch_failure` | 子进程无法启动 |
| `skipped_budget` | 预算耗尽，未执行 |

每条观测记录：输入长度、匹配结果、是否超时、编译耗时、匹配耗时、墙钟耗时，以及**进程启动开销**（`process_overhead_seconds ≈ 墙钟 − 编译 − 匹配`，与匹配耗时分开报告）。

## 默认预算（`Limits`）

| 字段 | 默认值 | 说明 |
|---|---|---|
| `max_pattern_length` | 2000 | 表达式长度上限 |
| `max_input_length` | 100000 | 单个输入长度上限（构造大字符串**之前**检查） |
| `max_candidates` | 24 | 候选样例数量上限 |
| `max_subprocess_launches` | 64 | 子进程启动次数上限 |
| `max_concurrent_processes` | 2 | 并发子进程数上限 |
| `total_time_seconds` | 15.0 | 本次分析总耗时上限 |
| `per_match_timeout_seconds` | 2.0 | 单次匹配期限（自动收紧到不超过剩余总预算） |
| `terminate_grace_seconds` | 1.0 | 终止后等待退出的宽限期，之后强制 kill |
| `seed` | 0 | 候选生成/缩减的固定种子，相同配置下顺序可复现 |

预算耗尽后停止提交新任务；每个在途任务的期限不超过本次分析剩余时间，因此会被自行终止并回收。

## 进程管理方式

- 每个样例在独立子进程中编译并匹配：`subprocess.Popen([sys.executable, "-m", "regex_risk.worker"])`，argv 列表形式，`shell=False`，通过 stdin/stdout 传 JSON。
- **超时由父进程控制**：到期后 `terminate()` → 宽限期 → `kill()` → 回收（wait），不是只取消 Future，也不等待匹配自行结束。
- 子进程卡住、提前退出、没有返回结果、结果传输失败，都会映射为可区分结果，调用方不会无限等待。
- 不 fork 任何携带状态的对象；worker 通过 `-m` 模块方式启动，兼容 Windows 的 **spawn** 启动方式。

## 平台支持

- **Linux**：已实际运行全部测试（WSL Ubuntu，Python 3.14.4，49 个测试通过）。
- **Windows**：按设计支持（spawn 安全、无 fork 依赖、无 shell、无 POSIX 专有 API；测试中的遗留进程检查使用 `/proc`，会自动跳过）。**未实际验证**。
- **macOS**：预期可用（与 Linux 相同的 POSIX 路径），**未实际验证**。

## 报告格式

版本化 JSON（`format_version: "1.0"`），包含：表达式、`flags`、实际使用的匹配方式、每个样例的**完整输入**、限制配置、Python 版本、全部观测结果、缩减记录、评估结论与注意事项。

- 读取时校验：文件大小（≤16 MiB）、JSON 结构、字段类型、格式版本、内嵌输入长度（≤1,000,000 且与实际一致）。校验失败抛出 `ReportError`。
- 不使用 pickle，不从报告中导入模块或执行任何代码。
- `replay_report` 会**重新执行**验证并返回全新结果，旧观测只用于复现配置与输入，绝不当作新结果返回。

## 超时样例缩减

发现超时样例后，在剩余预算内做 ddmin 风格的缩减；**每个缩减候选都在子进程中重新验证**，不继承原结论。报告同时保存原始样例与经过验证的缩减样例；缩减失败、结果不稳定或预算不足都会如实记录在 `note` 中，不保证得到最小样例。

## 已知局限

- 本库**不能**判定任意正则的复杂度，也**不能**证明不存在 ReDoS；结论只是有限预算内的证据。
- 不会仅凭"嵌套量词"等固定语法特征下结论，也不会把"没找到慢样例"解释为安全。
- 不会凭一次较慢或一次超时断言指数复杂度；`growth_hint` 只是基于不同长度观测值的线索。
- 机器负载、计时精度、解释器启动开销都会影响观测；进程启动开销与匹配耗时已分开报告，但小量级耗时本身噪声很大。
- 启发式候选生成覆盖有限；调用方提供针对业务输入的 `CandidateRule` 会得到更有意义的证据。
- 每个样例独占一个子进程，吞吐受进程启动开销限制。

## 运行测试

```bash
python -m unittest discover -s tests -v
```

全部测试离线运行，在临时目录中生成数据；受控的测试子进程（睡眠/崩溃/无输出）用于验证超时、终止与异常处理机制，真实 `re` 匹配路径单独覆盖。测试结束检查无遗留子进程。
