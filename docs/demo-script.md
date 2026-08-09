# 真实本地演示录制脚本

本脚本用于录制 IncidentPilot 的真实本地运行过程。不得把它描述为生产部署、私有 holdout 结果，或模型自主执行的写操作。

## 录制前准备

1. 确认 Docker Desktop 正在运行。本地 `.env` 只能包含运行所需的开发配置；不要在终端、浏览器或录屏中展示该文件。
2. 依次运行：

   ```powershell
   .\scripts\bootstrap_otel_demo.ps1
   .\scripts\start_dev.ps1
   ```

   如果默认端口已经被本机其他开发进程占用，使用隔离端口：

   ```powershell
   .\scripts\start_dev.ps1 -ApiHostPort 8201 -WebHostPort 5180
   ```

   正常完整体验应显示数据库、任务队列、Worker、Telemetry MCP 和 Action MCP 都是 `ready`。本地 Action MCP 只监听 loopback/Compose 内网，并且只暴露加密映射约束的 flagd rollback；它不是公开写入口。
3. 打开 Web 工作台，并准备一个只显示脱敏 health 输出的终端窗口。

## 两到三分钟演示流程

1. **发起真实案例。** 在“智能诊断”页保持“每次审批”，选择“结算依赖不可达”，点击“启动真实案例”。每次点击都会创建新的 Incident，不是历史结果回放。
2. **观察 AI 团队。** 单屏左侧会依次点亮分诊、Metrics/Logs/Traces/Runbook 并行调查和 Incident Commander。说明这些进度来自本次 Incident 的后端审计事件，不是前端倒计时动画；每个调查器只拥有范围受限的只读工具。
3. **查看诊断。** 右侧无需下滑就会出现根因、置信度、影响和 Evidence。需要技术深挖时点击“专业详情”，展示脱敏 Query、Evidence ID 和完整审计时间线。
4. **人工批准。** 中风险 rollback proposal 出现后，展示动作目标、风险和“签名审批 · 单次 nonce · 幂等执行”，由演示者点击“批准并执行”。模型不能批准自己，也不能修改 Policy Gate。
5. **恢复验证。** 观察授权、Action MCP 回滚和 60 秒 Prometheus 窗口，直到右侧显示“已验证恢复 / Prometheus SLO 已通过”。终态左侧安全门和受控进化都应为已完成。

## 只读演示与安全降级

如果模型凭据不可用，可以演示现有 scripted-model Incident 与已记录的公开评测产物；不得用 Mock telemetry 冒充实时演示。

如果只希望调查而不执行写操作，使用 `.\scripts\start_dev.ps1 -ReadOnly`，并在页面选择“仅诊断”。此时 Action MCP 为 `disabled` 是预期行为；不要把“提议”说成“已经执行”。正常 `start_dev.ps1` 会使用本地 `.env` 中已经独立配置的 Approval 与 private-mapping key 启动受控 actions profile。

## 录制检查清单

- 只展示 loopback URL；隐藏浏览器个人资料路径、包含 `.env` 的终端、密钥、Token 和私有目录。
- 展示真实 readiness 和 Evidence 页面，不使用剪辑过的截图代替运行结果。
- 明确标注“本地参考实现演示”。
- 结束前验证清理完成：所有 fault flag 为 `off`、没有活动任务、没有对公网暴露的 Action MCP endpoint。
