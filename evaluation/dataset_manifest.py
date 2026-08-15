"""Validate versioned evaluation datasets before reporting their metrics.

The repository deliberately keeps three different kinds of data apart:

* ``contract``: tiny, deterministic fixtures that protect invariants on every PR;
* ``smoke``: seeded end-to-end cases that catch integration regressions;
* ``candidate``: pinned real or synthetic cases still awaiting independent review;
* ``external_gold``: public benchmark labels created and reviewed by an external dataset team;
* ``production``: a frozen, independently reviewed corpus that may support
  externally reported quality claims.

Only the last tier is allowed to be described as production-representative.
The manifest pins a dataset's raw JSONL bytes with SHA-256 so a score can be
traced to the exact cases that produced it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_MANIFEST = ROOT / "evaluation" / "datasets" / "DATASET_MANIFEST.json"
VALID_TIERS = {"contract", "smoke", "candidate", "external_gold", "production"}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _jsonl_count(path: Path) -> int:
    return sum(1 for line in path.read_text(encoding="utf-8").splitlines() if line.strip() and not line.lstrip().startswith("#"))


def load_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or not isinstance(payload.get("datasets"), list):
        raise ValueError(f"{path} must contain a datasets list")
    return payload


def verify_manifest(path: Path = DEFAULT_MANIFEST) -> dict[str, Any]:
    """Return dataset integrity results without changing any dataset files."""

    manifest = load_manifest(path)
    errors: list[str] = []
    entries: list[dict[str, Any]] = []
    seen_ids: set[str] = set()

    for raw in manifest["datasets"]:
        if not isinstance(raw, dict):
            errors.append("dataset entry must be an object")
            continue
        dataset_id = str(raw.get("id", ""))
        tier = str(raw.get("tier", ""))
        relative_path = str(raw.get("path", ""))
        if not dataset_id or dataset_id in seen_ids:
            errors.append(f"duplicate or missing dataset id: {dataset_id!r}")
            continue
        seen_ids.add(dataset_id)
        if tier not in VALID_TIERS:
            errors.append(f"{dataset_id}: unsupported tier {tier!r}")
            continue
        dataset_path = ROOT / relative_path
        if not dataset_path.is_file():
            errors.append(f"{dataset_id}: missing dataset file {relative_path}")
            continue

        actual_sha256 = _sha256(dataset_path)
        actual_count = _jsonl_count(dataset_path)
        expected_sha256 = str(raw.get("sha256", "")).lower()
        expected_count = raw.get("case_count")
        if actual_sha256 != expected_sha256:
            errors.append(f"{dataset_id}: sha256 mismatch")
        if actual_count != expected_count:
            errors.append(f"{dataset_id}: expected {expected_count} cases, found {actual_count}")
        if tier == "production":
            for field in ("review_protocol", "frozen_at", "corpus_snapshot", "train_separation"):
                if not raw.get(field):
                    errors.append(f"{dataset_id}: production datasets require {field}")
        if tier == "candidate":
            if not raw.get("review_status"):
                errors.append(f"{dataset_id}: candidate datasets require review_status")
            if not raw.get("corpus_snapshot") and not raw.get("routing_snapshot"):
                errors.append(f"{dataset_id}: candidate datasets require corpus_snapshot or routing_snapshot")

        entries.append(
            {
                "id": dataset_id,
                "tier": tier,
                "case_count": actual_count,
                "sha256": actual_sha256,
                "claim_policy": (
                    "production_representative"
                    if tier == "production"
                    else "public_external_gold_not_online_traffic"
                    if tier == "external_gold"
                    else "candidate_not_reportable"
                    if tier == "candidate"
                    else "regression_only"
                ),
            }
        )

    return {
        "manifest": str(path.relative_to(ROOT)),
        "schema_version": manifest.get("schema_version"),
        "valid": not errors,
        "datasets": entries,
        "errors": errors,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify AlphaStock evaluation dataset manifest")
    parser.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    args = parser.parse_args()
    report = verify_manifest(args.manifest)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
