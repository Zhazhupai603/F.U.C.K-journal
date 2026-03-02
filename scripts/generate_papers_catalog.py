#!/usr/bin/env python3
"""Generate papers/papers.json from files in papers/ directory."""

from __future__ import annotations

import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PAPERS_DIR = ROOT / "papers"
OUTPUT_FILE = PAPERS_DIR / "papers.json"
DEFAULT_COVER_DIR = "assets/paper-covers"


def make_record(pdf_path: Path) -> dict[str, str]:
  paper_id = pdf_path.stem
  return {
    "id": paper_id,
    "title": paper_id,
    "pdfFile": pdf_path.name,
    "coverImage": f"{DEFAULT_COVER_DIR}/{paper_id}.jpg",
  }


def main() -> None:
  PAPERS_DIR.mkdir(parents=True, exist_ok=True)
  records = [
    make_record(path)
    for path in sorted(PAPERS_DIR.glob("*.pdf"))
  ]

  OUTPUT_FILE.write_text(
    json.dumps(records, ensure_ascii=False, indent=2) + "\n",
    encoding="utf-8",
  )
  print(f"Generated {OUTPUT_FILE} with {len(records)} paper(s).")


if __name__ == "__main__":
  main()
