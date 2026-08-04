"""训练四分类 fastText 意图模型，并输出独立验证集准确率。

运行：python scripts/train_intent_classifier.py
"""
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TRAIN_FILE = ROOT / "data" / "intent" / "train.txt"
VALID_FILE = ROOT / "data" / "intent" / "valid.txt"
MODEL_FILE = ROOT / "models" / "intent_classifier.bin"


def main():
    try:
        import fasttext
    except ImportError as exc:
        raise SystemExit("请先安装 fasttext-wheel：pip install fasttext-wheel") from exc
    if not hasattr(fasttext, "train_supervised"):
        raise SystemExit(
            "当前 fasttext 包仅支持预测，无法训练。请使用 requirements.txt 中的 "
            "fasttext-wheel，并在 Docker / Linux Python 3.11 环境执行本脚本。"
        )

    MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
    model = fasttext.train_supervised(
        input=str(TRAIN_FILE), epoch=80, lr=0.45, wordNgrams=2,
        minn=1, maxn=4, dim=64, loss="softmax", thread=1, seed=42,
    )
    model.save_model(str(MODEL_FILE))
    samples, precision, recall = model.test(str(VALID_FILE))
    print(f"模型已保存：{MODEL_FILE}")
    print(f"验证集：samples={samples}, precision@1={precision:.4f}, recall@1={recall:.4f}")


if __name__ == "__main__":
    main()
