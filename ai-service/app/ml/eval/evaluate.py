"""Offline evaluation of product-search accuracy used by the shopping assistant.

Run from ai-service:  python -m app.ml.eval.evaluate --output ../docs/bao-cao
Writes search_eval.csv and evaluation-summary.json.
"""
import argparse
import csv
import json
import random
from pathlib import Path

from app.services import catalog


def _variants(name: str) -> dict[str, str]:
    words = name.split()
    return {
        "exact": name,
        "no_accent_lower": catalog.normalize(name),
        "first_3_words": " ".join(words[:3]),
        "last_2_words": " ".join(words[-2:]) if len(words) > 2 else name,
        "typed_query": f"tôi muốn mua {name.lower()}",
    }


def evaluate_search(sample: int, seed: int) -> list[dict]:
    """Query the catalog with variants of each product name; success = the product is within top-k."""
    docs = catalog._load()
    chosen = random.Random(seed).sample(docs, min(sample, len(docs)))
    rows = []
    for doc in chosen:
        for kind, query in _variants(doc["name"]).items():
            ranked = [r["productId"] for r in catalog.search(query, None, 5)]
            rows.append({"productId": doc["productId"], "variant": kind, "top1": ranked[:1] == [doc["productId"]],
                         "top5": doc["productId"] in ranked})
    return rows


def _write(path: Path, rows: list[dict]) -> None:
    with path.open("w", encoding="utf-8", newline="") as f:
        if rows:
            writer = csv.DictWriter(f, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=Path("../docs/bao-cao"))
    parser.add_argument("--sample", type=int, default=200)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)

    search_rows = evaluate_search(args.sample, args.seed)
    _write(args.output / "search_eval.csv", search_rows)

    by_variant = {}
    for kind in sorted({r["variant"] for r in search_rows}):
        subset = [r for r in search_rows if r["variant"] == kind]
        by_variant[kind] = {"queries": len(subset), "top1": round(sum(r["top1"] for r in subset) / len(subset), 4),
                            "top5": round(sum(r["top5"] for r in subset) / len(subset), 4)}
    total = max(1, len(search_rows))
    summary = {
        "search": {"products_sampled": len({r["productId"] for r in search_rows}), "total_queries": len(search_rows),
                   "top1": round(sum(r["top1"] for r in search_rows) / total, 4),
                   "top5": round(sum(r["top5"] for r in search_rows) / total, 4), "by_variant": by_variant},
    }
    (args.output / "evaluation-summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
