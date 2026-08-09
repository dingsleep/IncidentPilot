from pathlib import Path

ROOT = Path(__file__).parents[2]


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").lower()


def test_delivery_documents_state_the_security_and_evaluation_boundaries() -> None:
    architecture = _read(ROOT / "docs" / "architecture.md")
    evaluation = _read(ROOT / "docs" / "evaluation.md")
    demo = _read(ROOT / "docs" / "demo-script.md")

    assert "not a free-form agent chat" in architecture
    assert "deterministic code" in architecture
    assert "holdout" in evaluation
    assert "\u4eba\u5de5\u6279\u51c6" in demo


def test_delivery_adrs_cover_the_required_design_decisions() -> None:
    decision_root = ROOT / "docs" / "decisions"
    assert "bounded" in _read(decision_root / "0002-bounded-multi-agent-graph.md")
    assert "human approval" in _read(decision_root / "0003-read-write-mcp-separation.md")
    assert "promotion" in _read(decision_root / "0004-controlled-evolution.md")


def test_final_report_is_explicit_about_unrun_holdout() -> None:
    final_report = _read(ROOT / "docs" / "reports" / "final-evaluation.md")
    limitations = _read(ROOT / "docs" / "reports" / "known-limitations.md")

    assert "holdout" in final_report
    assert "not run" in final_report
    assert "production" in limitations


def test_readme_points_readers_to_run_and_review_material() -> None:
    readme = _read(ROOT / "README.md")
    assert "30-second overview" in readme
    assert "docs/architecture.md" in readme
    assert "start_dev.ps1" in readme


def test_readme_has_a_chinese_job_facing_overview_and_architecture_visual() -> None:
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    architecture = ROOT / "docs" / "assets" / "incidentpilot-architecture.svg"
    demo_gif = ROOT / "GIF" / "演示动画.gif"

    assert "AI 事故响应团队" in readme
    assert "项目亮点" in readme
    assert "关键难题与工程优化" in readme
    assert "前端产品体验" in readme
    assert "真实评测" in readme
    assert "docs/assets/incidentpilot-architecture.svg" in readme
    assert "GIF/演示动画.gif" in readme
    assert architecture.is_file()
    assert demo_gif.is_file()
    assert "确定性安全门" in architecture.read_text(encoding="utf-8")


def test_ci_workflows_are_read_only_pinned_and_model_free() -> None:
    ci = _read(ROOT / ".github" / "workflows" / "ci.yml")
    evaluation = _read(ROOT / ".github" / "workflows" / "evaluation-smoke.yml")

    for workflow in (ci, evaluation):
        assert "permissions:\n  contents: read" in workflow
        assert "actions/checkout@11d5960a326750d5838078e36cf38b85af677262" in workflow
        assert "api_key" not in workflow
        assert "secrets." not in workflow

    assert "pytest tests/unit tests/contract" in ci
    assert "npm run typecheck" in ci
    assert "workflow_dispatch:" in evaluation
    assert "test_runner_orders_real_fault_episode_and_records_reproducibility" in evaluation


def test_windows_only_lock_dependencies_are_platform_guarded() -> None:
    lock = _read(ROOT / "requirements.lock")

    assert 'pywin32==312 ; sys_platform == "win32"' in lock
