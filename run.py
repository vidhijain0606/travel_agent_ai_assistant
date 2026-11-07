"""CLI entry point: run the pipeline against data/inquiries.json and
data/rate_sheet.csv, save results to output/proposals.json AND to one
output/<inquiry_id>.json file per inquiry.

Usage:
    python run.py
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from oltaflock.engine import load_rate_sheet  # noqa: E402
from oltaflock.pipeline import run_inquiry  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
OUTPUT_DIR = ROOT / "output"


def main() -> None:
    rate_sheet = load_rate_sheet(DATA_DIR / "rate_sheet.csv")
    inquiries = json.loads((DATA_DIR / "inquiries.json").read_text(encoding="utf-8"))

    OUTPUT_DIR.mkdir(exist_ok=True)
    results = []
    for inquiry in inquiries:
        print(f"\n=== {inquiry['id']} ===")
        result = run_inquiry(inquiry["id"], inquiry["text"], rate_sheet)
        result_json = result.model_dump(mode="json")
        results.append(result_json)
        print(result.model_dump_json(indent=2))

        per_inquiry_path = OUTPUT_DIR / f"{inquiry['id']}.json"
        per_inquiry_path.write_text(json.dumps(result_json, indent=2), encoding="utf-8")

    out_path = OUTPUT_DIR / "proposals.json"
    out_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    print(f"\nSaved {len(results)} per-inquiry files plus the combined {out_path}")


if __name__ == "__main__":
    main()
