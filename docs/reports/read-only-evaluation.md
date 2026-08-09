# M6.4 只读评测失败归因

日期：2026-07-19，更新于 2026-07-20（Asia/Shanghai）

## 结论

当前候选 **未达到** M6 只读质量门槛，不能晋级，也未运行冻结 holdout。

- validation-v1、`seed=22`、`deepseek-v4-flash` 非思考模式的公平对照中，baseline 根因准确率为 `0.500`，multi 为 `0.000`，均低于 `0.700`。
- baseline 与 multi 的 Evidence fidelity 均为 `0.750`，低于 `0.950`。该聚合值还包含 abstain 自动得满分的样本，不能解释为已有 Diagnosis 的引用有 75% 可信。
- multi 比 baseline 得分低 `0.179167`，成本增加 `5567 micro-USD`，模型耗时增加 `75427 ms`，工具调用数相同。
- 两种模式都没有安全硬失败，所有 Episode 都使用 3 个真实只读遥测工具，故障配置最终恢复，`paymentUnreachable=off`。
- 后续 Commander 单变量候选曾完整得到 `score=0.500`、root `0.250`、Evidence `0.750`，但它用 10 分钟前的陈旧 trace 把 recommendation 故障误归因到 `flagd`，因此被拒绝。收窄后的安全候选保留 false-alert abstention，却连续两次因 V4 Flash 返回 `parameter:extra_forbidden` 而无法完成完整 validation；不能冻结。
- public train 三 seed 稳定性对照中，V4 Flash 完成 `2/3` Episode，V4 Pro 完成 `3/3`；但首次 Schema 合法率分别只有 `9/12=75%` 和 `6/12=50%`，两者都低于 `95%`。Pro 依靠更多 repair 获得更高终局完成率，不能解释为首次结构遵循更强，也不能据此直接替换默认 profile。
- 显式 DeepSeek JSON Output 候选在同一 Flash/场景/seeds 下完成 `3/3` Episode，`12/12=100%` 首次 Schema 合法且无 Schema repair；成本和模型时延较 Flash Tool Strategy 分别降低 `45.6%` 和 `46.8%`。provider 格式门槛通过，但三例仍全部 abstain、root `0.000`，只允许进入 onset Query 单变量，不代表 M6 质量通过。
- 经用户确认的 alert-aligned Logs/Traces 候选把查询收窄到告警接收前 2 分钟至查询时刻。seed 31 两次均为 `score/root/Evidence=0.400/0.000/1.000`；修复 Jaeger 复用 trace ID 导致的越界 span 污染后，20 条 trace 全部在窗口内，但错误 trace 和错误日志仍均为 0，因此候选回退、seeds 32/33 早停。Jaeger 时间范围契约修复独立保留。
- 2026-07-20 的 scorer 审计确认旧 v1 对合法舍入和百分比换算存在假阴性。`scorer-v2` 离线复算不可变旧事实后，payment seeds31/32/33 的 root/Evidence 均为 `1.000/1.000`，taxonomy seed31 的 root/Evidence/category 为 `1.000/1.000/1.000`；这纠正了旧候选“Evidence 漂移”的归因，但不是新鲜 validation 结果，M6 仍未通过。

完整机器可读结果：

- [baseline report](../../artifacts/evaluations/eval-baseline-20260719090442-22/report.json)
- [multi report](../../artifacts/evaluations/eval-multi-20260719085900-22/report.json)
- [baseline vs multi](../../artifacts/evaluations/compare-v4-validation-seed22-20260719/report.json)
- [rejected Commander candidate](../../artifacts/evaluations/eval-multi-20260719091831-22/report.json)

## 公平对照

| 指标 | Baseline | Multi | Multi - Baseline |
|---|---:|---:|---:|
| Weighted score | 0.541667 | 0.362500 | -0.179167 |
| Root-cause accuracy | 0.500000 | 0.000000 | -0.500000 |
| Evidence fidelity | 0.750000 | 0.750000 | 0.000000 |
| Cost (micro-USD) | 3747 | 9314 | +5567 |
| Model/tool duration (ms) | 29730 | 105157 | +75427 |
| Read tool calls | 12 | 12 | 0 |
| Safety hard failures | 0 | 0 | 0 |

两种模式使用相同模型、validation 顺序、case seeds、服务范围、3 个只读工具、预算和真实 OTel Demo。multi 每例调用 Metrics、Logs、Traces 三个调查 Agent，再调用 Commander；baseline 每例只调用一个拥有同一只读工具全集的 Agent。

## 单变量迭代记录

| 运行 | 唯一变量 | 结果 | 归类 |
|---|---|---|---|
| `eval-multi-20260719084008-21` | 评测器兼容修正：原对象严格校验失败且仅含一个 `dict` 值时，再严格校验内层对象 | 修复 V4 Flash 返回单层 `value` 容器；合法单 `dict` 字段 Schema 有回归测试保护 | 工具 Schema |
| `eval-multi-20260719084950-22` → `eval-multi-20260719085355-22` | 只修改结构化 Tool description，明确必须调用一次且不得返回普通文本 | 前一运行的 `logs_investigator` 三次不调用工具不再复现；随后暴露 Commander 的 `parameter:extra_forbidden` | 工具选择、模型能力 |
| `eval-multi-20260719085355-22` → `eval-multi-20260719085714-22` | 只修改 Schema 失败后的 repair instruction，明确删除报错字段并禁止 wrapper/extra | 同一 `cart-failure-001`、同一 `seed=22` 定向 validation 通过，得分 0.400 | 工具 Schema、模型能力 |
| `eval-multi-20260719085900-22` | 不再调参，完整 validation 复跑 | 4/4 Episode 完成，保存为当前 multi 结果 | 完整验证 |
| `eval-multi-20260719091831-22` | 只修改 Commander Stop Conditions：false alert abstain，并在两种信号时推动故障 Diagnosis | control 从 0.250 升到 0.850，但 recommendation 被陈旧 trace 误导为 `flagd/dependency_failure`，与 `recommendation/cache_failure` 不符；候选拒绝 | 上下文、综合归因 |
| `eval-multi-20260719092605-22`、`eval-multi-20260719092758-22` | 只删除上述激进故障收敛句，保留 false-alert abstention | 两次不变候选分别在 cart、payment Commander 连续三次返回额外 `parameter` 而失败；第二次已完成 cart=0.400、control=0.850 后失败 | 工具 Schema、模型能力、稳定性 |
| `eval-multi-20260720064126-31` | Logs/Traces 改为 alert receipt 对齐的 2 分钟窗口，Metrics 不变 | 请求范围正确，但 Jaeger 返回从两天前开始、跨约 51 小时的复用 trace；score/root 仍为 0.400/0.000 | 工具时间契约、上下文 |
| `eval-multi-20260720064915-31` | 同一候选，仅修复 Jaeger 在响应端按请求窗口裁剪 spans | 20 条 trace 全部在窗口内、越界 0，但 error trace=0、error log=0；payment metric error ratio=0.025，仍不足两类实时信号，正确 abstain | 遥测缺失、上下文 |

`eval-multi-20260719084935-22` 是本地命令执行通道被 5 秒超时终止后产生的连接错误，不作为模型实验。所有失败运行均保留在数据库，没有重写为成功。

DeepSeek 官方文档说明严格 Function Schema 仍要求 `/beta` endpoint、`strict=true`，并要求每层 object 的全部属性都是 required；当前 `SynthesisDraft` 有合法可选字段，因此没有切换 Beta strict 或篡改领域契约。[DeepSeek Tool Calls strict mode](https://api-docs.deepseek.com/guides/tool_calls)

## 最终 validation 逐例归因

| 模式 / Episode | 结果 | 主要归类 | 事实依据 |
|---|---:|---|---|
| baseline / `cart-failure-001` | 0.400 | 遥测缺失、上下文、模型能力 | 有 Metric 候选但 Logs/Traces 未形成足够交叉证据，最终 abstain；工具过程、安全和效率满分。 |
| baseline / `no-fault-control-001` | 0.850 | 评分器问题 | 正确 abstain，根因、Evidence、信号、工具、安全和效率均满分；仅 recovery 失败。无故障例也 recovery 失败，不能归因于模型。 |
| baseline / `payment-unreachable-001` | 0.516667 | 遥测缺失、综合归因、模型能力 | 命中 `checkout`，但把类别归为自身 error ratio，未归因到 `payment` dependency unreachable；缺少 Logs，且引用中的数值声明与原始 Evidence 不一致。 |
| baseline / `recommendation-cache-leak-001` | 0.400 | 遥测缺失、上下文、模型能力 | 形成 `recommendation`/`flagd` 候选但证据不能满足终局门槛，最终 abstain。 |
| multi / `cart-failure-001` | 0.400 | 遥测缺失、上下文、综合归因 | 三个调查分支成功并形成 `cart`/`valkey-cart` 假设，但 Commander 未把 Metric + Trace 收敛为 Diagnosis。 |
| multi / `no-fault-control-001` | 0.250 | 综合归因、评分器问题 | Commander 明确写出“no customer impact / false alert”，却仍创建 `false-alert` Diagnosis，违反 expected-abstention 语义；评分器也没有把“确认无事故”的结构化结论与 abstain 语义统一。引用中的数值声明未通过 Evidence fidelity。 |
| multi / `payment-unreachable-001` | 0.400 | 遥测缺失、上下文、综合归因 | 三个调查分支成功，但 Logs 稀疏，候选集中在 `checkout`/`kafka`/`cart`，未建立 `checkout → payment` 的证据链，最终 abstain。 |
| multi / `recommendation-cache-leak-001` | 0.400 | 遥测缺失、上下文、综合归因 | 形成 `recommendation`/`flagd` 假设但缺少错误日志和明确传播 span，Commander abstain。 |

### 横向分类

- **环境：** 最终运行无环境阻断。OTel Demo smoke、PostgreSQL、Prometheus、OpenSearch、Jaeger、flagd UI 均可用，最终 flag 为 `off`。
- **遥测缺失：** 多例 Logs 为空或不足；当前一次性 15 分钟快照不能稳定提供故障 onset 前后对比，难以区分背景 error ratio 与注入故障。
- **工具 Schema：** V4 Flash 出现单层 wrapper 和额外 `parameter`；采用“先严格校验原对象、再有限 fallback”和有界 repair，不允许 `extra`。
- **工具选择：** 曾连续三次忽略 forced tool；强化 Tool description 后完整 validation 未复现。
- **上下文：** 三个 Evidence envelope 覆盖面宽但时间因果弱，缺少 onset 对齐、dependency path 聚合和健康基线。
- **综合归因：** multi 能产出候选假设，但 Commander 对故障例过度 abstain，对无故障例又把“false alert”保存成 Diagnosis。
- **模型能力：** V4 Flash 在 `temperature=0` 下仍表现出 Tool 参数和终局结构遵循波动；同一候选连续两次完整 validation 都因 `parameter:extra_forbidden` 失败，不能把此前一次成功当成确定性保证。
- **评分器问题：** 4/4 recovery 失败，包括没有注入的 control；30 秒窗口短于 span-metrics 稳定导出时间。另一个语义缺口是 false-alert Diagnosis 与 expected abstention 的等价/非等价尚未在契约中明确。当前分数原样保留，未放宽阈值。

## Public train V4 Flash/Pro 结构化稳定性对照

固定 `payment-failure-001`、multi、非思考 Tool Strategy、seeds `31/32/33`，两个 profile 使用相同 Prompt、Schema、工具、真实 OTel Demo 和有界三次尝试。首个代表性场景已经使两个 profile 的首次 Schema 合法率都低于门槛，因此按早停原则未扩展到其余三个 train 场景。

| Profile | Episode 完成 | 首次 Schema 合法 | repair 后逻辑调用成功 | Schema-invalid attempts | Provider attempts | Token | 成本 (micro-USD) | 模型时延 (ms) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| V4 Flash | 2/3 | 9/12 (75.0%) | 11/12 (91.7%) | 5 | 16 | 58,135 | 9,939 | 111,822 |
| V4 Pro | 3/3 | 6/12 (50.0%) | 12/12 (100%) | 7 | 19 | 60,923 | 31,529 | 154,514 |

逐 seed 数据库事实：

| Profile / seed | 终态 | 成功逻辑调用 | Schema-invalid attempts | Provider attempts | 成本 (micro-USD) |
|---|---|---:|---:|---:|---:|
| Flash / 31 | completed | 4 | 1 | 5 | 3,034 |
| Flash / 32 | completed | 4 | 1 | 5 | 3,103 |
| Flash / 33 | failed: `StructuredOutputError` | 3 | 3 | 6 | 3,802 |
| Pro / 31 | completed | 4 | 4 | 8 | 13,389 |
| Pro / 32 | completed | 4 | 1 | 5 | 8,555 |
| Pro / 33 | completed | 4 | 2 | 6 | 9,585 |

Flash seed 33 的 Commander 连续三次返回额外 `parameter`，对应失败 run `eval-multi-20260720054657-33`。完成报告为 `eval-multi-20260720054332-31`、`...054514-32`、`...054816-31`、`...055014-32`、`...055210-33`。所有完成 case 都为 `score=0.400`、root `0.000`、Evidence `1.000`；Pro 没有带来根因质量改善。价格使用 2026-07-20 再次核对的官方 cache-miss 费率：Flash `$0.14/$0.28`，Pro `$0.435/$0.87`（每百万输入/输出 Token）。[DeepSeek models and pricing](https://api-docs.deepseek.com/quick_start/pricing)

判定：V4 Pro 的 Episode 终局可靠性优于 Flash，但首次结构遵循更差、成本为 Flash 的 `3.17x`、模型时延为 `1.38x`。两个 profile 都未达到结构化稳定性门槛，停止扩大收费样本，不晋级 Pro，不进入 onset Query 实验。

### JSON Output provider candidate

唯一变量改为 `--structured-output-strategy json_output`，模型仍为 V4 Flash，场景、Prompt、Pydantic Schema、工具和 seeds 不变。默认 CLI 仍为 `tool_strategy`；候选版本显式记录为 `prompts-v1:deepseek-v4-flash:json_output`。

| seed | Episode | 首次 Schema 合法 | Schema repair | Provider attempts | Token | 成本 (micro-USD) | 模型时延 (ms) |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 31 | completed | 4/4 | 0 | 4 | 11,182 | 1,844 | 21,485 |
| 32 | completed | 4/4 | 0 | 5 | 11,029 | 1,809 | 22,003 |
| 33 | completed | 4/4 | 0 | 4 | 10,751 | 1,750 | 15,998 |
| 合计 | 3/3 | 12/12 (100%) | 0 | 13 | 32,962 | 5,403 | 59,486 |

seed 32 的额外 attempt 为一次 `CONNECTION_ERROR`，同一 Metrics 调用按既有网络重试策略成功，不是 Schema repair。对应报告：`eval-multi-20260720060759-31`、`eval-multi-20260720060928-32`、`eval-multi-20260720061056-33`。相对 Flash Tool Strategy，Token 降低 `43.3%`、成本降低 `45.6%`、模型时延降低 `46.8%`。

当时判定：JSON Output candidate 达到该轮 `>=95%` 首次 Schema 合法且无耗尽 repair 的门槛，保留为下一 train 实验的显式候选；在该阶段尚未经过完整 validation，默认策略不切换，候选不冻结，holdout 不运行。后续完整结果见本报告第 19-20 项。

### Rejected onset-delta metric candidate

在不修改原 `service_error_ratio` 的前提下，曾新增唯一模板 `service_error_ratio_onset_delta`，计算当前 2 分钟错误率减去 5 分钟前的 2 分钟基线，并把 query template digest 写入 candidate version。真实 Prometheus 解析和查询通过后，只运行 JSON Output + Flash + `payment-failure-001` seed 31：

- run `eval-multi-20260720062057-31`，candidate `prompts-v1:deepseek-v4-flash:json_output:queries-f7ff078e4f38`；
- score/root/Evidence 仍为 `0.400/0.000/1.000`，没有 Diagnosis；
- metric Evidence 中 payment/checkout onset delta 都为 `0.0`，部分服务为 `null`；同一快照的绝对错误率仅为 payment `0.032258`、checkout `0.010526`；
- 说明查询时 span-metrics 最新点尚未稳定吸收当前故障，固定 offset baseline 还会被本轮连续实验历史污染。

按预设早停规则没有运行 seeds 32/33。该模板、health snapshot wiring 和对应测试已回退，query digest 版本能力保留；失败 run 与 Evidence 原样保留。下一步若从“单一 metric Query template”转为“以 alert receipt time 收窄 Logs/Traces 查询并保留前置健康窗”，会改变已记录的实验路径，必须先向用户解释并确认。

### Rejected alert-aligned Logs/Traces candidate

用户确认路径偏移后，候选只把 Logs/Traces 从诊断时刻前 15 分钟改为“alert receipt 前 2 分钟至实际查询时刻”；Metrics 仍为既有 15 分钟 health snapshot，模型、Prompt、JSON Output、场景、seed、评分器和阈值均未改变。

- 首次 run `eval-multi-20260720064126-31` 为 `0.400/0.000/1.000`，cost `1,960 micro-USD`、模型耗时 `21,790 ms`。请求窗口为 `06:39:56Z–06:41:56Z`，但 Jaeger 返回从 2026-07-18 开始、持续约 51 小时的复用 trace，说明上游按相交范围返回 trace，而现有 backend 没有在 span 层落实 `TraceSearch` 时间契约。
- 失败测试 `test_jaeger_trims_reused_trace_ids_to_the_requested_time_range` 先复现越界 span；随后 Jaeger backend 只保留 `request.start <= span.startTime <= request.end` 的 spans，并用裁剪结果重算服务、错误、起点、时长和 span 数。真实无 Key 查询证明 20 条结果越界为 0。
- 修复后同 seed run `eval-multi-20260720064915-31` 仍为 `0.400/0.000/1.000`，cost `1,712 micro-USD`、模型耗时 `17,573 ms`。Logs 为 0 条；20 条 in-window traces 中 error trace 为 0；Metrics 中 payment error ratio 为 `0.025`、checkout 为 `0.007634`。Commander 因缺少第二类独立实时信号而 abstain，符合确定性 Stop Conditions。

按早停规则未运行 seeds 32/33。alert-aligned CLI 参数、时间构造和测试已回退，两个失败 artifact 与数据库事实保留；通用的 Jaeger 时间范围契约修复及其回归测试保留。结论从“窗口混入陈旧信号”收敛为“当前 30 秒预热下，故障在查询时尚未形成 error logs 或 in-window error traces”。

### Deterministic train traffic driver

无 LLM 受控实验在 `paymentFailure=100%` 下主动执行 6 次真实 checkout，得到 6/6 HTTP 5xx，随后两分钟 Jaeger 查询出现 6 条同时包含 checkout/payment 的 error traces，而 error logs 仍为 0；finally 后 flag 为 off。这证明业务失败、OTel trace 导出、Collector 和 Jaeger 链路均正常，缺失信号来自 M6 Episode 原先完全依赖约每分钟一次的后台 load-generator，30 秒窗口不保证命中 checkout。

经用户确认，train/validation 私有 `ExecutionSpec` 增加可选 `traffic` 契约；`payment-failure-001` 使用 `{adapter: otel-demo-http, operation: checkout, requests: 6}`。Runner 在 injection 激活后调用显式 driver；driver 先等待 3 秒让 Payment 的 flag provider 刷新，再要求 6 次 checkout 全部返回 5xx，任何未命中或网络错误都会使 Episode 失败并进入原有 cleanup。随后仍执行原场景的 30 秒 warmup，让 trace/metric 正常导出。该字段不进入 `RuntimeEpisodeInput`、Alert、Prompt、Tool output 或公开 API。

真实 Runner 回归已证明至少 6 条 in-window error traces 同时包含 checkout/payment 且 flag 精确恢复；Schema/Loader/Runner/driver 专项先后为 19 passed 和 15 passed，Ruff/Pyright 通过。场景执行语义变化后 train suite 升为 `train-v2`，validation 保持 `validation-v1`。

首个 train-v2 同 Flash + JSON Output + payment + seed31 run `eval-multi-20260720072707-31` 仍为 `score/root/Evidence=0.400/0.000/1.000`，cost `1,782 micro-USD`、模型耗时 `16,850 ms`。与 train-v1 不同，Evidence 已明确出现 payment/checkout error ratio `0.301471/0.136667`，20 条 traces 中 11 条为 error；Commander 生成 confidence 0.7 的 payment 假设并同时引用 metric/trace Evidence，但仍因“缺少 payment span 细节”而 abstain。根因是 `TraceSummary` 只给 trace-level error 和全部 service 集合，无法区分 error 属于哪个服务/operation。

以失败测试增加有界 `error_services` 和最多 12 个去重 `error_spans(service, operation, status_code)` 后，真实 Jaeger Evidence 已明确包含 `payment / grpc.oteldemo.PaymentService/Charge / ERROR`；工具契约升为 `telemetry-v2` 并写入 candidate version。`eval-multi-20260720074122-31` 中 Commander 已形成 confidence 0.85 的 payment 假设并引用 metric/trace，但仍把缺少日志/配置当作终局阻断，score/root/Evidence 仍为 `0.400/0.000/1.000`。

随后把 Commander 通用终止契约明确为 confidence≥0.75、至少两类实时 Evidence、无矛盾即可诊断，缺少可选第三信号写入 `diagnosis_limits`；并明确少于三个 hypothesis 合法、无 Evidence 占位项必须省略。Prompt set digest 进入 candidate version。`eval-multi-20260720080120-31` 与 `eval-multi-20260720081057-31` 暴露 DeepSeek 连续三次输出空 `supporting_evidence_ids`；失败 run 原样保留。通用 repair instruction 以 `s-v2` 修正为：missing/too_short 不删除必填字段，无依据的嵌套可选项整体删除且不得编造值。

当前 public train 最优 run `eval-multi-20260720081613-31` 使用 Prompt digest `3a7b09c45fb4`：4/4 模型调用均首次 Schema 合法，score/root/Evidence=`0.700/1.000/1.000`，cost `2,422 micro-USD`、模型耗时 `21,159 ms`、3 个只读工具、0 安全硬失败。Diagnosis 为 `payment`、confidence 0.85，并引用真实 metric/trace Evidence；失分为模型输出未规范化类别 `service_error` 和 M7 尚未实现导致 recovery=0。

只增加公共 taxonomy 的候选 `eval-multi-20260720082258-31` 正确输出 `dependency_failure`。旧 `scorer-v1` 因它把原始 ratio 以百分比和合理舍入表述而判定 `UNSUPPORTED_EVIDENCE_CLAIM`；逐项审计确认数值来自类型化 Evidence，`scorer-v2` 复算为 root/Evidence/category=`1.000/1.000/1.000`、总分 `0.850`。旧 artifact 保持原样，该拒绝结论由本段明确纠正。

`scorer-v2` 不做模糊语义放行：只读取原始 JSON 的数值字段，允许与最后展示位一致的舍入，百分比先除以 100，整数必须精确；测试证明错误的 `31.8%` 以及仅出现在 digest/string 中的数字仍失败。suite version 升为 `*-score-v2`，避免新旧评分口径混合。

validation 执行规格升为 v2：cart 固定 6 次真实 EmptyCart、payment 固定 6 次 checkout、recommendation 固定 20 次请求并要求至少一次真实 5xx，否则 Episode 硬失败；这没有改变上游 50% cache 故障概率。查询窗口从当前 Episode 起点开始，Metrics 使用最小 1 分钟窗口。`telemetry-v3` 仅增加有界 allowlist cache observations，不暴露用户 ID、产品列表或任意 span tag。真实无 LLM 回归已验证三条流量路径和 cleanup，但最终 LLM 候选尚未重新运行。

## 门槛判定与下一实验

| 门槛 | 当前结果 | 判定 |
|---|---:|---|
| Root-cause service accuracy ≥ 70% | 最终 validation-v2 `eval-multi-20260729104213-31`：100% | 达到 |
| Evidence reference validity ≥ 95% | 最终 validation-v2：100% | 达到 |
| 无安全硬失败 | 最终 validation-v2：0 | 达到 |
| Fault taxonomy（附加发布门槛） | `taxonomy-v1` 独立 train 15/15、validation 10/10；Qwen recommendation 接线回归 category=1 | 达到组件门槛 |
| 最终候选跨场景稳定性 | Qwen 完整 public train 在首个 ad case 因 Commander 连续三次生成空 `diagnosis.evidence_ids` 失败 | 未达到冻结要求 |

候选不冻结，不运行 holdout，也不继续对旧 validation-v1 反复抽样。最终候选必须按预先固定顺序执行：

1. provider 结构化契约单变量已完成：JSON Output 达到 `100%` 首次 Schema 合法，保留本地 Pydantic 严格校验；Beta strict 因当前合法 nullable 字段与官方支持类型约束不兼容而未采用。
2. onset-delta metric 和 alert-aligned Logs/Traces 两个候选均已单 seed 早停并回退；后者同时修复了 Jaeger reused trace ID 的越界 span 污染，但确认当前故障窗口没有 error logs 或 error traces。
3. scorer-v2、validation deterministic traffic v2、Episode 查询隔离、`telemetry-v3` 和 Commander/taxonomy 已完成无 LLM 或离线事实验证；旧 payment 三 seed 的根因与引用稳定，但这不代表完整 validation 达标。
4. 固定 Prompt digest `4eda8bfa3927` + query digest `cc9086987f03` + `telemetry-v3` + `s-v2` + `scorer-v2`，已于 2026-07-21 新鲜运行 payment seeds31/32/33：`eval-multi-20260721045252-31`、`...045421-32`、`...045546-33` 每例 total=0.800，root/Evidence/category 均为 1，safety=1、硬失败=0、flag=off。
5. 随后的唯一完整 validation-v2 `eval-multi-20260721052608-31` 失败：cart 错归因 frontend-proxy，no-fault 正确 abstain，payment Commander 连续三次 `diagnosis.evidence_ids:too_short`，recommendation 未运行。cart 根因 span 事后才在 514–654 秒结束并导出，超过 600 秒 Episode 预算；运行中 Evidence 不足以支持隐藏答案。该结果不补跑、不聚合成通过。
6. 经用户确认，本地 Demo overlay 已为 `flagd` 增加默认网络 alias `badhost`，让 cartFailure 的硬编码 `badhost:1234` 立即解析并拒绝连接；同一场景现于约 5 秒内返回 HTTP 500，并在预算内产生 `POST /oteldemo.CartService/EmptyCart` ERROR span。没有改变 ground truth、Episode 预算或评分门槛。
7. 上游 `.env` 的 `DEMO_VERSION=latest` 会在单个服务重建时静默混入不兼容的新镜像；overlay 已显式固定全部 20 个应用镜像为 2.2.0，未修改 `.env`。真实 cart、正常 checkout、OTel smoke、全量 pytest 159 passed/1 skipped、Ruff、Pyright、pip check 和 alembic check 均通过。
8. 环境修复不等于模型通过。新增显式 Qwen provider：非思考 JSON Output 使用 `enable_thinking=false`，按官方建议不发送 `max_tokens`，本地 Pydantic 严格校验保持不变；价格按 provider/model 和输入阶梯计算，避免沿用 DeepSeek 价格。
9. `qwen3.7-flash` 同条件 payment seed31 run `eval-multi-20260729052320-31` 的四个 Agent 调用均首次 Schema SUCCESS；root/Evidence/safety=`1/1/1`、硬失败 0，但 category=`0`，把 ground truth `dependency_failure` 判为 `application_failure`，total=`0.650`。因此 seeds32/33 早停，格式稳定不能替代语义分类质量。
10. `qwen3.7-plus` 同条件 payment seed31 run `eval-multi-20260729053449-31` 同样四次首次 Schema SUCCESS，root/Evidence/safety=`1/1/1`、硬失败 0，但仍把显式 taxonomy 要求的 `dependency_failure` 判为 `application_failure`，category=`0`、total=`0.650`、cost=`6737` micro-USD、duration=`48910` ms；因此 seeds32/33 早停。
11. Flash 与 Plus 在相同输入上同类失败，已经足以排除模型档位是主因；取消 Max 实验。保持 Qwen 3.7 Flash、场景、工具、query、scorer 和 ground truth 不变，只把 Commander taxonomy 从并列定义改为通用判定优先级，没有写 payment/checkout 专例。Prompt candidate `7539174767b7` 的 seed31 run `eval-multi-20260729055604-31` 仍为 root/Evidence/safety=`1/1/1`、category=`0`、total=`0.650`；Diagnosis 仍输出 symptom=`checkout`、root=`payment`、dependency=`null`、category=`application_failure`。因此停止 Prompt 微调、seeds32/33 早停并回退 active Prompt `4eda8bfa3927`；回退后相关 Prompt/LLM/CLI 专项 40 passed，Ruff、Pyright 通过。
12. 经用户明确批准架构偏移，把第 12.3 节和 Commander Prompt 已有语义落实为 `Diagnosis` 领域不变量：跨服务诊断不得使用 `application_failure`，错误类型固定为 `cross_service_application_failure`，由既有结构化网关记录 `SCHEMA_INVALID` 并在最多两次 repair 内要求模型重答；不自动改写模型输出。首次真实 run `eval-multi-20260729061514-31` 中三调查成功，但 Commander 三次均因该不变量被拒绝，run 以 `StructuredOutputError` 失败；这证明约束有效，也暴露通用 repair 只有错误码而缺少可执行语义。随后以失败测试为该固定错误码增加固定领域说明：跨服务下游返回错误时使用 `dependency_failure`，并在有依据时填写 `dependency_service`；其他 repair 不变。最终全量 pytest 165 passed/1 skipped，Ruff check/format、Pyright、pip check 和 Alembic check 通过。
13. 完整 validation 保持 root≥70%、Evidence≥95%、安全硬失败为 0；失败就归因，不降阈值、不改 ground truth、不删样本、不把 recovery=0 解释为模型根因能力达标。
14. candidate version 已覆盖 Prompt set digest、Tool version、repair/schema version、provider strategy 和 Query template digest；模型 provider revision 与完整代码 commit 仍未覆盖，因为仓库按用户要求尚未 commit。
15. 用户明确要求停止剪贴板式 Key 传递。根目录现有 Git 忽略的 `.env`，分别使用 `INCIDENTPILOT_LLM_QWEN_API_KEY` 和 `INCIDENTPILOT_LLM_DEEPSEEK_API_KEY`；运行时按当前 provider 自动选择，显式 `INCIDENTPILOT_LLM_API_KEY` 仍可作为进程级覆盖。对 Instructor、HolmesGPT、AIOpsLab、ITBench、DSPy 和 GEPA 的官方资料对照记录在 [model-optimization-research.md](model-optimization-research.md)；随后已按计划完成语义 repair、RCA/taxonomy 拆分和完整 validation，结果见第 16-20 项。
16. RCA synthesis/taxonomy 职责拆分后，payment 根因与类别由通用服务依赖图、normalized `name_resolution_error` 和“调用端错误且无目标 server span”规则闭环；`eval-multi-20260729075015-33` 的 root/category/Evidence 均为 1。
17. Prometheus backend 过滤非有限值，避免合法 `NaN` p95 使整个 metric Evidence 序列化失败；spanmetrics Collector 固定 15 秒刷新和稳定 resource key，评测窗口使用 2 分钟。新增 `container_memory_usage` 仅证明容器在同窗被观测，不把缺失 RED 指标自动解释为不可达。
18. recommendation 的 `not_found` 与 cache hit/miss observation 被归一化为有界因果证据；`eval-multi-20260729081149-34` 得分 1.0。cart 原始 `FailedPrecondition / Redis connection` 只映射为 `storage_connection_failure`，不把原始错误文本交给模型；`eval-multi-20260729103805-31` 得分 1.0。
19. 最终完整 run `eval-multi-20260729104213-31` 使用 candidate `prompts-v1-32c4cdd3b760:qwen3.7-flash:json_output:queries-2f310decbea1:tools-telemetry-v8:s-v3`。4/4 case 完成，aggregate=`0.954167`、root/Evidence=`1.0/1.0`、安全硬失败 0、cost 2528 micro-USD、duration 74756 ms、12 个只读工具调用；19/19 ModelCall 首次 SUCCESS。case 为 cart=`1.0`、control=`1.0`、payment=`0.966667`、recommendation=`0.85`。
20. recommendation 唯一失分是 taxonomy：服务、Evidence、signal、recovery 和 safety 全部正确，但 cache miss 路径被判为 `application_failure`。Prompt 优先级候选 `eval-multi-20260729105154-34` 和 causal-mechanism 确定性映射候选 `eval-multi-20260729105735-34` 均仍为 0.85，已回退。该失败不是通过换 API 模型或重复 seed 解决的问题；最终候选不冻结，不运行 holdout，不进入 M7。
21. 新增与 Episode validation 服务完全隔离的 `taxonomy-v1`：train 15 例、validation 10 例，五类各自平衡覆盖；manifest 固定文件 SHA256。标注优先级明确为“cache hit 成功且 cache miss 失败”优先于本地错误处理缺陷，其后依次为 rate limit、storage application failure、dependency unreachable、跨服务 dependency failure、同服务 application failure。train=15/15、首次 validation=10/10，validation 文件在首次运行后未修改。
22. 在线链路只从有界 Metric/Trace `ToolEnvelope` 提取结构化 facts，并按 RCA root service 重新限定 cache observation；最终类别由版本化确定性 policy 组装，Commander 不再承担 taxonomy 输出，也没有第二次 taxonomy LLM 调用。回归测试覆盖无关服务 cache 信号不得污染 root。candidate version 一度超过数据库 `VARCHAR(100)`，失败测试复现 `103 > 100` 后改为等价短标签编码，仍完整保留 Prompt/model/strategy/query/tool/schema 版本。
23. 非门槛端到端接线回归保留全部结果：误用 `.env` 当前 DeepSeek 配置的 `eval-multi-20260729114053-41` 为 total/root/Evidence=`0.55/0/1`，Commander 将 root 指向 `product-catalog` 后 abstain；一次只覆盖 provider、未同步 base URL/model 的错误组合 run `eval-multi-20260729114416-41` 以 `ModelProviderError` 失败。正确以进程级三项配置固定 Qwen 3.7 Flash 的 `eval-multi-20260729114626-41` 为 total/root/Evidence/category/safety/recovery=`1/1/1/1/1/1`，4/4 ModelCall 首次 SUCCESS、3 个只读工具、flag=off。
24. 为验证新 RCA schema 不只适配 recommendation，随后运行完整 public train seed41。run `eval-multi-20260729114917-41` 在首个 `Advertisement requests are failing` case 失败：三调查器均 SUCCESS，但 Commander 连续三次 `diagnosis.evidence_ids:too_short`，run 以 `StructuredOutputError` 结束，四个公开 flag 均为 off。因此 taxonomy 缺陷已经闭环，但最终候选仍不冻结，M6 保持进行中，M7 和私有 holdout 均不开始；下一工程问题是把调查报告 Evidence 到终局 Diagnosis 的绑定改成可验证的确定性组装，而不是增加 retry 或放宽 Evidence Schema。

## 复现命令

2026-07-29 之前的运行只从剪贴板注入子进程，运行后清除。现在由用户在 Git 忽略的
`.env` 中一次性填写 provider 专用槽位；命令不再读取剪贴板，也不得输出 Key：

```powershell
& 'D:\software\ana\envs\tx_agent\python.exe' .\scripts\run_eval.py --mode multi --split validation --seed 22 --no-actions
& 'D:\software\ana\envs\tx_agent\python.exe' .\scripts\run_eval.py --mode baseline --split validation --seed 22 --no-actions
```

稳定性对照把 split/scenario 改为 `--split train --scenario payment-failure-001`，分别使用 `--model-profile fast|strong` 和 seeds `31/32/33`；必须串行运行并在每例后确认 `paymentFailure=off`。

JSON Output 对照在上述命令追加 `--structured-output-strategy json_output`，只使用 fast profile；默认不变。

冻结 holdout 未读取、未解密、未搜索、未运行。
