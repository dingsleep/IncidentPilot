from scripts import benchmark


def test_percentile_uses_nearest_rank_for_short_samples() -> None:
    assert benchmark.percentile([1.0, 2.0, 3.0, 4.0, 5.0], 0.95) == 5.0


def test_memory_growth_reports_largest_container_delta() -> None:
    before = {"api": 100.0, "worker": 50.0}
    after = {"api": 108.5, "worker": 48.0}

    assert benchmark.memory_growth_mib(before, after) == 8.5


def test_persistent_growth_requires_every_sample_to_increase() -> None:
    snapshots = [
        {"api": 100.0, "worker": 50.0},
        {"api": 110.0, "worker": 47.0},
        {"api": 120.0, "worker": 52.0},
    ]

    assert benchmark.persistent_growth_mib(snapshots) == {"api": 20.0}
