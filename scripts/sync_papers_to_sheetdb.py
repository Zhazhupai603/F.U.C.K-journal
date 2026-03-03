#!/usr/bin/env python3
"""Sync local papers catalog to SheetDB without manual row editing.

Usage:
  python scripts/sync_papers_to_sheetdb.py \
    --api https://sheetdb.io/api/v1/ev8cjfow1i90w \
    --source papers/papers.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def request_json(
    url: str, method: str = "GET", payload: dict | None = None
) -> tuple[int, str]:
    data = None
    headers = {"Content-Type": "application/json"}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = Request(url=url, method=method, data=data, headers=headers)
    try:
        with urlopen(req, timeout=20) as resp:
            body = resp.read().decode("utf-8", errors="ignore")
            return resp.status, body
    except HTTPError as exc:
        body = exc.read().decode("utf-8", errors="ignore")
        return exc.code, body
    except URLError as exc:
        return 0, str(exc)


def load_rows(source: Path) -> list[dict]:
    raw = json.loads(source.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("Source JSON must be an array")
    rows = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        row = {
            "id": str(item.get("id", "")).strip(),
            "serial": str(item.get("serial", "")).strip(),
            "title": str(item.get("title", "")).strip(),
            "doi": str(item.get("doi", "")).strip(),
            "authors": str(item.get("authors", "")).strip(),
            "pdfFile": str(item.get("pdfFile", "")).strip(),
            "coverImage": str(item.get("coverImage", "")).strip(),
            "date": str(item.get("date", "")).strip(),
            "volume": str(item.get("volume", "")).strip(),
            "abstract": str(item.get("abstract", "")).strip(),
            "keywords": ";".join(item.get("keywords", []))
            if isinstance(item.get("keywords"), list)
            else str(item.get("keywords", "")).strip(),
            "downloads": str(item.get("downloads", 0)).strip()
            if item.get("downloads") is not None
            else "0",
        }
        if row["id"] and row["title"] and row["pdfFile"]:
            rows.append(row)
    return rows


def upsert_row(api: str, row: dict) -> tuple[bool, str]:
    row_id = row["id"]
    for endpoint in (f"{api}/id/{row_id}", f"{api}/search?id={row_id}"):
        for method in ("PATCH", "PUT"):
            code, body = request_json(endpoint, method=method, payload={"data": row})
            if 200 <= code < 300:
                return True, f"{method} {endpoint} ({code})"

    code, body = request_json(api, method="POST", payload={"data": row})
    if 200 <= code < 300:
        return True, f"POST {api} ({code})"
    return False, f"FAILED id={row_id}: POST {code} {body[:160]}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync papers.json to SheetDB")
    parser.add_argument("--api", required=True, help="SheetDB API URL")
    parser.add_argument(
        "--source", default="papers/papers.json", help="Local papers JSON file"
    )
    args = parser.parse_args()

    api = args.api.rstrip("/")
    source = Path(args.source)
    rows = load_rows(source)
    print(f"Loaded {len(rows)} rows from {source}")

    ok_count = 0
    for row in rows:
        ok, msg = upsert_row(api, row)
        print(msg)
        if ok:
            ok_count += 1

    print(f"Done. Success: {ok_count}/{len(rows)}")


if __name__ == "__main__":
    main()
