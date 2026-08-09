from __future__ import annotations

import argparse
import json
from pathlib import Path

from incidentpilot.evaluation.taxonomy import (
    evaluate_taxonomy_suite,
    load_taxonomy_suite,
    verify_taxonomy_manifest,
)

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description="Run deterministic taxonomy evaluation")
    parser.add_argument("--split", choices=("train", "validation"), required=True)
    args = parser.parse_args()
    suite_root = ROOT / "scenarios" / "taxonomy"
    policy_version = verify_taxonomy_manifest(suite_root / "manifest.json", ROOT)
    cases = load_taxonomy_suite(
        suite_root / f"{args.split}.yaml",
        expected_split=args.split,
    )
    result = evaluate_taxonomy_suite(cases)
    print(
        json.dumps(
            {"policy_version": policy_version, "split": args.split, **result.model_dump()},
            sort_keys=True,
        )
    )
    return int(result.accuracy < 1)


if __name__ == "__main__":
    raise SystemExit(main())
