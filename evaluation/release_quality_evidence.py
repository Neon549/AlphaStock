"""Verify that every measured release-gate value is backed by a frozen artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from evaluation.financial_agent_e2e import load_jsonl
from evaluation.financial_agent_e2e_gold_freeze import _canonical_jsonl_hash
from evaluation.release_quality_gate import evaluate_release_quality_gate


SCHEMA_VERSION = "alpha-stock-release-quality-evidence/v1"


def _file_sha256(path: Path) -> str:
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _resolve_pointer(value: Any, pointer: str) -> Any:
    if pointer == "":
        return value
    if not pointer.startswith("/"):
        raise ValueError("JSON pointer must start with /")
    current = value
    for raw in pointer[1:].split("/"):
        token = raw.replace("~1", "/").replace("~0", "~")
        current = current[int(token)] if isinstance(current, list) else current[token]
    return current


def _gate_value(report: dict[str, Any], dotted: str) -> Any:
    current: Any = report
    for token in dotted.split("."):
        current = current[token]
    return current


def _required_sources(gate_input: dict[str, Any]) -> set[str]:
    required: set[str] = set()
    checks = gate_input.get("checks", {})
    for family in ("rag", "e2e", "citation"):
        for metric, values in checks.get(family, {}).get("metrics", {}).items():
            required.add(f"checks.{family}.metrics.{metric}.candidate")
            required.add(f"checks.{family}.metrics.{metric}.baseline")
    required.update({
        "checks.latency.p95_ms",
        "checks.cost.mean_cost_usd",
        "checks.cost.mean_tokens",
        "checks.red_team.total_cases",
        "checks.red_team.high_risk_failures",
    })
    return required


def verify_release_evidence(spec: dict[str, Any], *, root: Path) -> dict[str, Any]:
    errors: list[str] = []
    if spec.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    gate_input = spec.get("gate_input")
    if not isinstance(gate_input, dict):
        gate_input = {}
        errors.append("gate_input must be an object")
    artifacts = spec.get("artifacts")
    if not isinstance(artifacts, dict):
        artifacts = {}
        errors.append("artifacts must be an object")

    loaded: dict[str, Any] = {}
    resolved_paths: dict[str, Path] = {}
    root = root.resolve()
    for name, metadata in artifacts.items():
        if not isinstance(metadata, dict):
            errors.append(f"artifact {name} metadata must be an object")
            continue
        relative = Path(str(metadata.get("path", "")))
        path = (root / relative).resolve()
        try:
            path.relative_to(root)
        except ValueError:
            errors.append(f"artifact {name} escapes repository root")
            continue
        if not path.is_file():
            errors.append(f"artifact {name} is missing: {relative}")
            continue
        actual_hash = _file_sha256(path)
        if actual_hash != metadata.get("sha256"):
            errors.append(f"artifact {name} sha256 mismatch")
        try:
            loaded[name] = (
                load_jsonl(path)
                if metadata.get("format") == "jsonl"
                else json.loads(path.read_text(encoding="utf-8"))
            )
            resolved_paths[name] = path
        except (json.JSONDecodeError, ValueError):
            errors.append(f"artifact {name} must match its declared JSON/JSONL format")

    roles = spec.get("gold_artifacts", {})
    required_roles = {"manifest", "cases", "reviews", "runs"}
    if not isinstance(roles, dict) or not required_roles.issubset(roles):
        errors.append("gold_artifacts must map manifest, cases, reviews and runs")
    else:
        manifest = loaded.get(str(roles["manifest"]))
        if not isinstance(manifest, dict) or not manifest.get("valid") or manifest.get("dataset_tier") != "production":
            errors.append("Gold freeze manifest is not valid production evidence")
        elif not manifest.get("admission", {}).get("dataset_admission_ready"):
            errors.append("Gold production admission is incomplete")
        else:
            for role, manifest_key in (("cases", "cases_sha256"), ("reviews", "reviews_sha256"), ("runs", "runs_sha256")):
                artifact_name = str(roles[role])
                path = resolved_paths.get(artifact_name)
                if path is None:
                    continue
                try:
                    actual = _canonical_jsonl_hash(load_jsonl(path))
                except ValueError as exc:
                    errors.append(f"Gold {role} artifact is invalid: {exc}")
                    continue
                if actual != manifest.get("artifacts", {}).get(manifest_key):
                    errors.append(f"Gold {role} hash does not match freeze manifest")
            if gate_input.get("candidate_version") != manifest.get("dataset_id"):
                errors.append("candidate_version must equal the frozen Gold dataset_id")

    sources = spec.get("metric_sources")
    if not isinstance(sources, dict):
        sources = {}
        errors.append("metric_sources must be an object")
    required_sources = _required_sources(gate_input)
    for metric_path in sorted(required_sources):
        source = sources.get(metric_path)
        if not isinstance(source, dict):
            errors.append(f"missing metric source: {metric_path}")
            continue
        artifact_name = str(source.get("artifact", ""))
        artifact = loaded.get(artifact_name)
        try:
            observed = _resolve_pointer(artifact, str(source.get("pointer", "")))
            declared = _gate_value(gate_input, metric_path)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            errors.append(f"invalid metric source {metric_path}: {type(exc).__name__}")
            continue
        if isinstance(declared, (int, float)) and isinstance(observed, (int, float)):
            matches = math.isclose(float(declared), float(observed), rel_tol=0, abs_tol=1e-12)
        else:
            matches = declared == observed
        if not matches:
            errors.append(f"metric source mismatch: {metric_path}")

    gate = evaluate_release_quality_gate(gate_input)
    if not gate["release_allowed"]:
        errors.append("release quality gate did not pass")
    return {
        "schema_version": "alpha-stock-release-quality-evidence-result/v1",
        "valid": not errors,
        "release_allowed": not errors and gate["release_allowed"],
        "verified_artifacts": sorted(loaded),
        "verified_metric_sources": len(required_sources) - sum(
            error.startswith("missing metric source:") or error.startswith("invalid metric source") or error.startswith("metric source mismatch:")
            for error in errors
        ),
        "errors": errors,
        "gate": gate,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify evidence behind an AlphaStock release gate")
    parser.add_argument("--spec", type=Path, required=True)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    report = verify_release_evidence(json.loads(args.spec.read_text(encoding="utf-8")), root=args.root)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["release_allowed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
