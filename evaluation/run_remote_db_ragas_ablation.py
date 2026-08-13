"""Run RAGAS over pre-generated remote-database retrieval samples."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.evaluator import run_ragas_eval


ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SAMPLES = ROOT / "runtime" / "reports" / "remote-db-ragas-samples.json"
DEFAULT_OUT = ROOT / "runtime" / "reports" / "remote-db-ragas-ablation.json"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path, default=DEFAULT_SAMPLES)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    samples = json.loads(args.samples.read_text(encoding="utf-8"))
    results = {}
    for method, rows in samples.items():
        results[method] = run_ragas_eval(rows, method)
        print(method, json.dumps(results[method], ensure_ascii=False), flush=True)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"out={args.out}", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
