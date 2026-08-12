from __future__ import annotations

import json
from pathlib import Path

from evaluation.select_cfqa_mapping_batch import main


def test_select_cfqa_mapping_batch_is_deterministic(tmp_path: Path, monkeypatch) -> None:
    source = tmp_path / "source.json"
    source.write_text(
        json.dumps(
            [
                {"股票代码": "600519", "公司": "贵州茅台", "问题": f"问题{i}", "答案": f"答案{i}", "答案出自": [[i + 1]], "id": i}
                for i in range(5)
            ],
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    output = tmp_path / "out.jsonl"
    monkeypatch.setattr("sys.argv", ["select", "--source", str(source), "--output", str(output), "--count", "3", "--seed", "7"])
    assert main() == 0
    first = output.read_text(encoding="utf-8")
    assert main() == 0
    assert output.read_text(encoding="utf-8") == first
    assert len(first.splitlines()) == 3
