# M6 模型优化调研与实验决策

日期：2026-07-29

## 当前问题

固定真实 OTel Demo、payment 场景、Evidence、Prompt、JSON Output、query、scorer 和 seed 后，
`qwen3.7-flash` 与 `qwen3.7-plus` 都能稳定定位 `payment` 并引用有效 Evidence，但都把跨服务
故障归为 `application_failure`，而不是公开 taxonomy 要求的 `dependency_failure`。只改
taxonomy Prompt 后错误不变，因此继续换更大 API 模型或重复抽样缺少依据。

## 与成熟实现和研究的对照

| 来源 | 可复用做法 | IncidentPilot 当前状态 |
|---|---|---|
| [Instructor retry mechanisms](https://python.useinstructor.com/learning/validation/retry_mechanisms/) | 捕获结构/语义校验错误，把具体反馈加入上下文，并限制重试次数；持续失败必须保留失败尝试和 usage | 已有 Pydantic 严格校验、逐次 ModelCall、最多两次 repair；最新改动把固定 taxonomy 错误翻译为可执行语义反馈 |
| [Instructor custom validators](https://python.useinstructor.com/learning/validation/custom_validators/) | 客观规则用确定性 validator；复杂主观语义才交给模型，同时权衡额外成本和时延 | “跨服务不得为 application_failure”是客观规则，已进入 `Diagnosis` 领域不变量；不由评分器事后放宽 |
| [HolmesGPT](https://github.com/HolmesGPT/holmesgpt) | 服务端过滤、JSON tree traversal、tool output transformer 和输出预算，避免把大遥测原文塞入上下文；生产调查默认只读并尊重 RBAC | Metrics/Logs/Traces 只获得各自最小只读工具；Commander 只接收有界 typed reports，不接收完整遥测原文 |
| [AIOpsLab](https://github.com/microsoft/AIOpsLab) | 真实微服务、故障注入、工作负载、遥测和 agent trace 统一编排；评价 solution、trace、duration，不只看最终文本 | Episode Runner 已覆盖真实故障、确定性 traffic、真实遥测、清理、轨迹、成本和时延 |
| [ITBench](https://github.com/itbench-hub/ITBench) | 用真实风格 SRE 场景、可解释指标和可复现环境评估 Agent；论文报告强模型仍只解决 13.8% SRE 场景，说明不能靠单次漂亮输出证明可用 | 保持 root、Evidence、安全和完整 validation 门槛；不因任务困难而降阈值或删除失败场景 |
| [DSPy optimizers](https://github.com/stanfordnlp/dspy/blob/main/docs/docs/learn/optimization/optimizers.md) | 少量样本先做 few-shot；更多数据才做 instruction search；优化必须绑定确定性 metric 和独立 validation | 当前只有少量公开场景家族，暂不引入 DSPy 依赖或自动搜索，避免在同一 payment 样本上过拟合 |
| [GEPA](https://arxiv.org/abs/2507.19457) | 用失败轨迹和文字反馈提出、评测 prompt candidate；仍需训练/验证分离和候选晋级 | M9 已设计候选生成、离线回归、影子评测和人工批准；M6 不提前做在线自改写 |

## 决策

1. 不切 DeepSeek，不再做 Qwen Max/更多 seed 的盲目模型搜索；当前 provider 固定
   `qwen3.7-flash`。
2. 先只运行一次已经完成的 taxonomy 语义 repair：
   `payment-failure-001`、seed 31、其余变量不变。它与 Instructor 的“validator +
   specific feedback + bounded retry”做法一致。
3. 若 repair 成功，才按既定 seeds 31/32/33 验证稳定性，再决定是否进入完整
   validation；单次成功不算 M6 通过。
4. 若 repair 仍连续失败，不增加 retry 次数、不继续措辞微调。下一候选把 Commander
   拆成两个可审计步骤：
   - RCA synthesis 只决定 symptom、root、dependency、Evidence 和置信度；
   - taxonomy decision 只接收上述小型 typed facts 和公开类别规则，输出单一类别。
   两步都保留原始模型输出、成本和失败记录，最终 `Diagnosis` 仍由领域不变量验证。
5. 只有积累跨场景、独立 split 的足量失败/成功轨迹后，才评估 BootstrapFewShot 或
   GEPA；微调仍受 M10 的数据与 GPU 门槛约束。当前数据量不足以证明权重训练优于任务拆分。

## 不采用的捷径

- 不根据 ground truth 自动改写模型类别；
- 不把领域 validator 的拒绝计为模型答对；
- 不降低 root/Evidence 门槛，不删除 cart/recommendation；
- 不读取冻结 holdout 来调参；
- 不用重复采样挑一次偶然成功结果。

## 2026-07-29 实验结论

RCA synthesis 与独立 taxonomy 拆分、失败类型归一化、服务依赖上下文、2 分钟 metric
窗口、15 秒 spanmetrics 刷新以及非有限 Prometheus 值过滤后，完整 validation
`eval-multi-20260729104213-31` 达到 aggregate `0.954167`、root/Evidence `1.0/1.0`、
安全硬失败 0，19/19 ModelCall 首次 Schema 成功。工程因果语义修复明显优于继续升级
Qwen 档位。

剩余边界集中在 recommendation：RCA 同时包含“cache miss 触发”和“本地没有妥善处理
dependency not_found”两个真实描述。模型稳定选择后者的 `application_failure`，而评测
ground truth 要求 `cache_failure`。重排 taxonomy Prompt 与新增 causal-mechanism →
category 确定性映射均未改变选择，两个候选已回退。

因此停止在同一 validation case 上继续调 Prompt 或抽 seed。下一步需要新增独立的
train/validation taxonomy 对照样本，明确“触发路径”和“缺陷位置”的标注优先级；在该
标注策略版本化前，不冻结候选、不运行 holdout，也不引入 DSPy/GEPA 自动搜索。这样保留
本次 root/Evidence 的真实进步，同时不把 2/3 fault taxonomy 包装成模型已完全可用。

## 2026-07-29 taxonomy-v1 结果与新阻断

已新增完全独立于 Episode validation 服务名的 taxonomy train/validation，五类分别为
`cache_failure`、`upstream_rate_limit`、`dependency_unreachable`、
`dependency_failure` 和 `application_failure`。train 15/15、首次 validation 10/10，
manifest 固定两个 suite 的 SHA256；validation 首次运行后没有修改。policy 明确规定：
观察到 cache hit 成功且 cache miss 失败时，触发路径优先标为 `cache_failure`，即使
RCA 同时描述了本地错误处理缺陷。

线上实现不把 expected category 或 suite 内容交给模型。它只从实际 ToolEnvelope 提取
有界事实，并按 RCA root service 限定 cache observation，再由版本化确定性 policy 组装
最终类别。正确配置的 Qwen 3.7 Flash recommendation 接线回归
`eval-multi-20260729114626-41` 得分 1.0，root、Evidence、category、safety 和 recovery
均为 1，4 次 ModelCall 首次成功。

这仍不足以冻结最终候选。完整 public train `eval-multi-20260729114917-41` 在首个 ad
case 中，三个调查器成功，但 Commander 连续三次输出空 `diagnosis.evidence_ids`，
严格 Schema 正确拒绝并终止 run。结论是 taxonomy 边界已解决，剩余风险转为
“调查 Evidence 到终局 Diagnosis 的稳定绑定”。下一步不应增加 retry 或放宽 Evidence
门槛，而应评估由确定性代码从同 root 的已验证 hypothesis/report 引用组装 Diagnosis
Evidence，并保留模型负责 RCA 语义判断。
