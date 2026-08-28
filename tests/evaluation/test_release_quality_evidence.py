import hashlib
import json

from evaluation.financial_agent_e2e_gold_freeze import _canonical_jsonl_hash
from evaluation.release_quality_evidence import SCHEMA_VERSION, verify_release_evidence


def _write_json(path, value):
    path.write_text(json.dumps(value), encoding="utf-8")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def _spec(tmp_path):
    cases = [{"id": "case-1"}]
    reviews = [{"case_id": "case-1"}]
    runs = [{"case_id": "case-1"}]
    paths = {}
    for name, rows in (("cases", cases), ("reviews", reviews), ("runs", runs)):
        path = tmp_path / f"{name}.jsonl"
        paths[name] = path
        _write_jsonl(path, rows)
    metrics = {
        "rag_candidate": 0.71, "rag_baseline": 0.70,
        "e2e_candidate": 0.82, "e2e_baseline": 0.81,
        "citation_candidate": 0.91, "citation_baseline": 0.90,
        "p95_ms": 1800, "mean_cost_usd": 0.02, "mean_tokens": 1600,
        "red_total": 40, "red_failures": 0,
    }
    metrics_path = tmp_path / "metrics.json"
    _write_json(metrics_path, metrics)
    manifest = {
        "valid": True, "dataset_tier": "production", "dataset_id": "real-gold-v1",
        "admission": {"dataset_admission_ready": True},
        "artifacts": {
            "cases_sha256": _canonical_jsonl_hash(cases),
            "reviews_sha256": _canonical_jsonl_hash(reviews),
            "runs_sha256": _canonical_jsonl_hash(runs),
        },
    }
    manifest_path = tmp_path / "manifest.json"
    _write_json(manifest_path, manifest)
    gate = {
        "candidate_version": "real-gold-v1", "baseline_version": "real-gold-v0",
        "checks": {
            "code_regression": {"passed": True}, "governance_regression": {"passed": True},
            "rag": {"metrics": {"recall_at_10": {"candidate": .71, "baseline": .70}}},
            "e2e": {"metrics": {"success_rate": {"candidate": .82, "baseline": .81}}},
            "citation": {"metrics": {"citation_accuracy": {"candidate": .91, "baseline": .90}}},
            "latency": {"p95_ms": 1800, "max_p95_ms": 2500},
            "cost": {"mean_cost_usd": .02, "max_mean_cost_usd": .05, "mean_tokens": 1600, "max_mean_tokens": 2500},
            "red_team": {"total_cases": 40, "high_risk_failures": 0},
        },
    }
    artifacts = {}
    for name, path in {**paths, "manifest": manifest_path, "metrics": metrics_path}.items():
        artifacts[name] = {"path": path.name, "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()}
        if name in paths:
            artifacts[name]["format"] = "jsonl"
    mapping = {
        "checks.rag.metrics.recall_at_10.candidate": "rag_candidate",
        "checks.rag.metrics.recall_at_10.baseline": "rag_baseline",
        "checks.e2e.metrics.success_rate.candidate": "e2e_candidate",
        "checks.e2e.metrics.success_rate.baseline": "e2e_baseline",
        "checks.citation.metrics.citation_accuracy.candidate": "citation_candidate",
        "checks.citation.metrics.citation_accuracy.baseline": "citation_baseline",
        "checks.latency.p95_ms": "p95_ms", "checks.cost.mean_cost_usd": "mean_cost_usd",
        "checks.cost.mean_tokens": "mean_tokens", "checks.red_team.total_cases": "red_total",
        "checks.red_team.high_risk_failures": "red_failures",
    }
    return {
        "schema_version": SCHEMA_VERSION, "gate_input": gate, "artifacts": artifacts,
        "gold_artifacts": {"manifest": "manifest", "cases": "cases", "reviews": "reviews", "runs": "runs"},
        "metric_sources": {path: {"artifact": "metrics", "pointer": f"/{key}"} for path, key in mapping.items()},
    }


def test_evidence_gate_accepts_hash_bound_metrics(tmp_path):
    result = verify_release_evidence(_spec(tmp_path), root=tmp_path)
    assert result["release_allowed"] is True
    assert result["verified_metric_sources"] == 11


def test_evidence_gate_rejects_a_hand_edited_metric(tmp_path):
    spec = _spec(tmp_path)
    spec["gate_input"]["checks"]["e2e"]["metrics"]["success_rate"]["candidate"] = .99
    result = verify_release_evidence(spec, root=tmp_path)
    assert result["release_allowed"] is False
    assert any("metric source mismatch" in error for error in result["errors"])


def test_evidence_gate_rejects_tampered_artifact(tmp_path):
    spec = _spec(tmp_path)
    (tmp_path / "metrics.json").write_text("{}", encoding="utf-8")
    result = verify_release_evidence(spec, root=tmp_path)
    assert result["release_allowed"] is False
    assert any("sha256 mismatch" in error for error in result["errors"])
