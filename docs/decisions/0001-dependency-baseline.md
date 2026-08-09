# ADR 0001：依赖基线

日期：2026-07-15  
状态：已接受

## 决策

IncidentPilot 使用 Python 3.12、`pyproject.toml` 声明的主版本边界和
`requirements.lock` 中的精确解析版本。解析与安装固定使用官方 PyPI。

`cryptography` 的边界从 `>=45,<47` 调整为 `>=45,<50`，锁定
`49.0.0`。这是经用户明确授权的规范偏离：共享 `tx_agent` 环境中的
`qqmusic-api-python 0.6.7` 要求 `cryptography>=47`；原上限会使
`pip check` 失败。该变更不改变读写 MCP 隔离、审批或评测原则。

## 来源与解析结果

核对日期为 2026-07-15。来源均为官方 PyPI 元数据：

- https://pypi.org/project/langgraph/ ：`langgraph==1.2.9`，支持 Python 3.12。
- https://pypi.org/project/langchain-mcp-adapters/0.3.0/ ：`langchain-mcp-adapters==0.3.0`。
- https://pypi.org/project/SQLAlchemy/ ：`sqlalchemy==2.0.51`，支持 Python 3.12。
- https://pypi.org/project/cryptography/49.0.0/ ：`cryptography==49.0.0`，支持 Python 3.12。

关键解析版本还包括：`langchain==1.3.13`、
`langgraph-checkpoint-postgres==3.1.0`、`alembic==1.18.5`、
`asyncpg==0.31.0`、`pydantic-settings==2.14.2`、`fastapi==0.139.0`、
`mcp==1.28.1`、`opentelemetry-sdk==1.43.0`。

## 兼容性与差异

- 原宿主基线中的 FastAPI `0.138.2` 和 OpenAI SDK `2.44.0` 分别解析为
  `0.139.0` 和 `2.45.0`；均处于项目声明的主版本边界内。
- `cryptography` 跨越 47/48/49 三个主版本，因此后续涉及 JWT、密钥、
  holdout 加密或 TLS 的实现必须先有测试，再依赖其 API；M0 仅完成导入 smoke，
  尚未声明业务行为兼容。
- 已验证 `alembic`、`asyncpg`、`cryptography`、FastAPI、LangChain、
  LangGraph、MCP、SQLAlchemy 等模块可导入，且
  `AsyncPostgresSaver` 可导入。

## 验证

```powershell
& $PYTHON -m piptools compile --upgrade --extra dev --strip-extras --output-file requirements.lock pyproject.toml
& $PYTHON -m pip install --index-url https://pypi.org/simple -r requirements.lock
& $PYTHON -m pip check
```

以上命令均成功；`pip check` 输出 `No broken requirements found`。
