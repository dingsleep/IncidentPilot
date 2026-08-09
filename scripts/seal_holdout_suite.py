from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any, cast

import yaml

from incidentpilot.evaluation.holdout_crypto import seal_holdout_suite
from incidentpilot.evaluation.loader import verify_holdout_manifest

ROOT = Path(__file__).parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Seal an explicitly provided private holdout suite"
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    source = args.source.resolve()
    output = args.output.resolve()
    expected_output_root = (ROOT / "artifacts" / "private").resolve()
    if source == ROOT.resolve() or ROOT.resolve() in source.parents:
        parser.error("--source must be outside the project workspace")
    if expected_output_root not in output.parents or output.suffixes[-2:] != [".json", ".enc"]:
        parser.error("--output must be a .json.enc file under artifacts/private")
    if output.exists():
        parser.error("refusing to overwrite an existing frozen holdout bundle")

    passphrase = os.environ.pop("INCIDENTPILOT_HOLDOUT_KEY", "")
    if not passphrase:
        parser.error("INCIDENTPILOT_HOLDOUT_KEY is required in the current process")
    public_dir = ROOT / "scenarios" / "holdout"
    manifest_path = public_dir / "suite-manifest.json"
    public_digests = verify_holdout_manifest(manifest_path, ROOT)
    cases: list[dict[str, Any]] = []
    for path in sorted(source.glob("case-h*.private.yaml")):
        raw: Any = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            parser.error(f"{path.name} must contain an object")
        cases.append(cast(dict[str, Any], raw))

    sealed = seal_holdout_suite(cases, passphrase=passphrase, public_digests=public_digests)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_bytes(sealed.payload)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["status"] = "SEALED"
    manifest["private_bundle_path"] = output.relative_to(ROOT).as_posix()
    manifest["private_bundle_sha256"] = sealed.digest
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    print(f"Sealed {len(cases)} holdout cases; bundle_sha256={sealed.digest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
