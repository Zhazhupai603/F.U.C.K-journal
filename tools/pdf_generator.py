#!/usr/bin/env python3
"""Generate branded journal PDFs from Tencent export + DOCX submissions."""

from __future__ import annotations

import argparse
import csv
import difflib
import io
import json
import re
import shutil
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from collections.abc import Iterable
from typing import TypedDict, cast

from docx import Document
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Flowable,
    Image as RLImage,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
)


BRAND_PRIMARY = colors.HexColor("#2c3e50")
BRAND_ACCENT = colors.HexColor("#e74c3c")

HEADER_MAP = {
    "submission_id": "编号",
    "title_zh": "1.论文标题",
    "authors": "2.作者姓名",
    "affiliation": "3.作者学院/机构",
    "abstract_zh": "5.论文摘要",
    "keywords_zh": "6.关键词",
}

ATTACHMENT_HEADERS = [f"7.论文文件_附件{i}" for i in range(1, 10)]


@dataclass
class Submission:
    submission_id: str
    title_zh: str
    title_en: str
    authors: str
    affiliation: str
    abstract_zh: str
    abstract_en: str
    keywords_zh: str
    keywords: list[str]
    keywords_en: str
    docx_path: Path
    body_paragraphs: list[str]
    images: list[bytes]


@dataclass
class RenderedPaper:
    submission: Submission
    doi: str
    doi_id: str
    generated_pdf: Path
    published_pdf: Path


class DoiState(TypedDict):
    year: int
    next_seq: int
    assignments: dict[str, str]


class DoiManager:
    def __init__(self, state_file: Path, year: int, start_seq: int) -> None:
        self.state_file: Path = state_file
        self.year: int = year
        self.start_seq: int = start_seq
        self.state: DoiState = self._load_state()

    def _load_state(self) -> DoiState:
        if self.state_file.exists():
            raw = cast(
                dict[str, object],
                json.loads(self.state_file.read_text(encoding="utf-8")),
            )
            year_raw = raw.get("year", self.year)
            next_raw = raw.get("next_seq", self.start_seq)
            year = int(year_raw) if isinstance(year_raw, (int, str)) else self.year
            next_seq = (
                int(next_raw) if isinstance(next_raw, (int, str)) else self.start_seq
            )
            raw_assignments = raw.get("assignments", {})
            assignments = (
                cast(dict[str, str], raw_assignments)
                if isinstance(raw_assignments, dict)
                else {}
            )
            return {"year": year, "next_seq": next_seq, "assignments": assignments}
        return {"year": self.year, "next_seq": self.start_seq, "assignments": {}}

    def _save_state(self) -> None:
        self.state_file.parent.mkdir(parents=True, exist_ok=True)
        self.state_file.write_text(
            json.dumps(self.state, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def assign(self, submission_id: str) -> str:
        if self.state["year"] != self.year:
            self.state = {
                "year": self.year,
                "next_seq": self.start_seq,
                "assignments": {},
            }

        assignments = self.state["assignments"]
        if submission_id in assignments:
            return assignments[submission_id]

        seq = self.state["next_seq"]
        doi = f"10.FUCK/{self.year}.{seq:03d}"
        assignments[submission_id] = doi
        self.state["next_seq"] = seq + 1
        self._save_state()
        return doi


def register_fonts() -> None:
    pdfmetrics.registerFont(
        TTFont(
            "JournalCJK",
            "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
            subfontIndex=0,
        )
    )
    pdfmetrics.registerFont(
        TTFont(
            "JournalCJKBold",
            "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
            subfontIndex=0,
        )
    )


def read_rows(csv_path: Path) -> list[dict[str, str]]:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            with csv_path.open("r", encoding=encoding, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError:
            continue
    raise ValueError("Cannot decode CSV. Supported: utf-8-sig / gb18030")


def parse_hyperlink_formula(cell: str) -> tuple[str, str]:
    match = re.search(
        r'Hyperlink\("([^"]+)"\s*[，,]\s*"([^"]+)"\)', cell, flags=re.IGNORECASE
    )
    if not match:
        return "", ""
    return match.group(1), match.group(2)


def normalize_display_name(name: str) -> str:
    return re.sub(r"^\d+_q\d+_论文文件_", "", name)


def split_keywords(raw: str) -> list[str]:
    tokens = [t.strip() for t in re.split(r"[，,;；、]\s*", raw) if t.strip()]
    return tokens


def find_docx_path(row: dict[str, str], words_dir: Path) -> Path:
    for header in ATTACHMENT_HEADERS:
        cell = (row.get(header) or "").strip()
        if not cell:
            continue

        _, display_name = parse_hyperlink_formula(cell)
        candidates = [display_name, normalize_display_name(display_name)]
        for c in candidates:
            if not c:
                continue
            p = words_dir / c
            if p.exists():
                return p

    raise FileNotFoundError(
        f"Cannot locate DOCX for submission {row.get('编号', '?')}. "
        f"Put file into {words_dir} and keep name consistent with attachment display name."
    )


def is_front_matter_heading(title: str) -> bool:
    low = title.strip().lower()
    return low in {"摘要", "关键词", "abstract", "keywords"}


def strip_duplicated_front_matter(
    body: list[str],
    abstract_zh: str,
    keywords_zh: str,
    title_zh: str,
    authors: str,
    affiliation: str,
) -> list[str]:
    abstract_norm = re.sub(r"\s+", "", abstract_zh)
    _ = keywords_zh
    filtered: list[str] = []
    skip_section = False
    text_line_index = 0
    author_tokens = [
        token.strip() for token in re.split(r"[，,、;；\s]+", authors) if token.strip()
    ]

    for line in body:
        if line.startswith("## "):
            heading = line[3:].strip()
            skip_section = is_front_matter_heading(heading)
            if not skip_section:
                filtered.append(line)
            continue

        if skip_section:
            continue

        if line.startswith("[[IMAGE:"):
            filtered.append(line)
            continue

        text_line_index += 1
        stripped = line.strip()
        compact = re.sub(r"\s+", "", line)
        if re.match(
            r"^[【\[]?\s*(关键词|key\s*words?)\s*[】\]]?\s*[:：]?",
            line.strip(),
            flags=re.IGNORECASE,
        ):
            continue

        if text_line_index <= 20:
            if stripped == title_zh.strip() or stripped == affiliation.strip():
                continue
            if stripped == authors.strip() or stripped in author_tokens:
                continue
            if re.match(r"^【?摘要】?", stripped):
                continue
            if re.match(r"^(作者|单位|机构)\s*[:：]", stripped):
                continue

        if abstract_norm and compact and len(compact) >= 20:
            if compact in abstract_norm or abstract_norm in compact:
                continue
            similarity = difflib.SequenceMatcher(None, compact, abstract_norm).ratio()
            if similarity >= 0.92:
                continue
            if text_line_index <= 20 and similarity >= 0.75:
                continue

        filtered.append(line)

    original_text_lines = [
        line
        for line in body
        if line and (not line.startswith("## ")) and (not line.startswith("[[IMAGE:"))
    ]
    filtered_text_lines = [
        line
        for line in filtered
        if line and (not line.startswith("## ")) and (not line.startswith("[[IMAGE:"))
    ]
    if len(original_text_lines) >= 6 and len(filtered_text_lines) <= 2:
        return body

    return filtered


def extract_docx_body_and_images(docx_path: Path) -> tuple[list[str], list[bytes]]:
    doc = Document(str(docx_path))
    body: list[str] = []
    images: list[bytes] = []

    for p in doc.paragraphs:
        text = (p.text or "").strip()
        if not text:
            pass
        else:
            style_name = p.style.name if p.style and p.style.name else ""
            if style_name.lower().startswith("heading"):
                body.append(f"## {text}")
            else:
                body.append(text)

        for run in p.runs:
            embeds = run._element.xpath(".//a:blip/@r:embed")
            for rel_id in embeds:
                part = doc.part.related_parts.get(rel_id)
                if not part:
                    continue
                images.append(part.blob)
                body.append(f"[[IMAGE:{len(images) - 1}]]")

    return body, images


def image_flowable(image_blob: bytes, max_width: float) -> RLImage:
    bio = io.BytesIO(image_blob)
    reader = ImageReader(bio)
    width, height = reader.getSize()
    flow = RLImage(io.BytesIO(image_blob))
    scale = min(1.0, max_width / float(width))
    flow.drawWidth = float(width) * scale
    flow.drawHeight = float(height) * scale
    return flow


def slugify(text: str) -> str:
    s = re.sub(r"\s+", "-", text.strip())
    s = re.sub(r"[^\w\-\u4e00-\u9fff]", "", s)
    return s[:60] or "paper"


def doi_to_id(doi: str) -> str:
    return doi.replace("/", "-")


def ensure_catalog_entry(site_root: Path, paper: RenderedPaper) -> None:
    catalog_path = site_root / "papers" / "papers.json"
    if catalog_path.exists():
        data = json.loads(catalog_path.read_text(encoding="utf-8"))
        catalog = cast(list[dict[str, object]], data) if isinstance(data, list) else []
    else:
        catalog = []

    today = datetime.now().strftime("%Y-%m-%d")
    enriched: dict[str, object] = {
        "id": paper.doi_id,
        "title": paper.submission.title_zh,
        "pdfFile": f"{paper.doi_id}.pdf",
        "coverImage": f"assets/paper-covers/{paper.doi_id}.jpg",
        "authors": paper.submission.authors,
        "doi": paper.doi,
        "date": today,
        "volume": "Vol. 1, Issue 2",
        "abstract": paper.submission.abstract_zh or "该论文摘要暂未录入。",
        "keywords": paper.submission.keywords or ["F.U.C.K", "Paper", "Undergraduate"],
        "affiliation": paper.submission.affiliation,
        "titleEn": paper.submission.title_en,
        "abstractEn": paper.submission.abstract_en,
        "keywordsEn": paper.submission.keywords_en,
    }

    existing_index = next(
        (
            idx
            for idx, item in enumerate(catalog)
            if str(item.get("id", "")).strip() == paper.doi_id
        ),
        -1,
    )
    if existing_index >= 0:
        catalog[existing_index] = enriched
    else:
        catalog.append(enriched)

    catalog.sort(key=lambda x: str(x.get("id", "")))
    catalog_path.write_text(
        json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def ensure_paper_list_html(site_root: Path, paper: RenderedPaper) -> None:
    paper_html = site_root / "paper.html"
    text = paper_html.read_text(encoding="utf-8")
    if f"paper-detail.html?id={paper.doi_id}" in text:
        return

    card = (
        f'      <a class="paper-item-link" href="paper-detail.html?id={paper.doi_id}" aria-label="查看论文详情：{paper.submission.title_zh}">\n'
        f'        <div class="paper-item">\n'
        f"          <h3>{paper.submission.title_zh}</h3>\n"
        f"          <p>作者：{paper.submission.authors} | DOI: {paper.doi}</p>\n"
        '          <span class="paper-more">View Paper Details <i class="bi bi-arrow-right"></i></span>\n'
        "        </div>\n"
        "      </a>\n"
    )

    marker = "    </section>"
    idx = text.find(marker)
    if idx < 0:
        raise ValueError("paper.html section marker not found")
    paper_html.write_text(text[:idx] + card + text[idx:], encoding="utf-8")


def ensure_home_list_html(site_root: Path, paper: RenderedPaper) -> None:
    index_html = site_root / "index.html"
    text = index_html.read_text(encoding="utf-8")
    if paper.submission.title_zh in text and paper.doi in text:
        return

    block_match = re.search(
        r'<div id="papers">(?P<body>[\s\S]*?)\n\s*</div>\n\s*</section>', text
    )
    if not block_match:
        raise ValueError("index.html papers block not found")

    insertion = (
        '        <div class="paper">\n'
        f"          <h3>{paper.submission.title_zh}</h3>\n"
        f"          <p>作者：{paper.submission.authors} | DOI: {paper.doi}</p>\n"
        f"          <p>摘要：{paper.submission.abstract_zh or '（摘要待补充）'}</p>\n"
        "        </div>\n"
    )
    body = block_match.group("body")
    updated_block = f'<div id="papers">{body}\n{insertion}      </div>\n    </section>'
    full = text[: block_match.start()] + updated_block + text[block_match.end() :]
    index_html.write_text(full, encoding="utf-8")


def publish_paper(site_root: Path, paper: RenderedPaper) -> None:
    papers_dir = site_root / "papers"
    papers_dir.mkdir(parents=True, exist_ok=True)
    if paper.generated_pdf.resolve() != paper.published_pdf.resolve():
        shutil.copy2(paper.generated_pdf, paper.published_pdf)
    ensure_catalog_entry(site_root, paper)
    ensure_paper_list_html(site_root, paper)
    ensure_home_list_html(site_root, paper)


def sheetdb_request_json(
    url: str, method: str = "GET", payload: dict[str, object] | None = None
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


def normalize_sheetdb_row(item: dict[str, object]) -> dict[str, str]:
    keywords_raw = item.get("keywords", "")
    if isinstance(keywords_raw, list):
        keywords = ";".join(str(v).strip() for v in keywords_raw)
    else:
        keywords = str(keywords_raw).strip()

    return {
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
        "keywords": keywords,
        "downloads": str(item.get("downloads", "0")).strip() or "0",
    }


def sync_catalog_to_sheetdb(site_root: Path, api_url: str) -> tuple[int, int]:
    catalog_path = site_root / "papers" / "papers.json"
    if not catalog_path.exists():
        return 0, 0

    raw = json.loads(catalog_path.read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        return 0, 0

    rows = [
        normalize_sheetdb_row(cast(dict[str, object], item))
        for item in raw
        if isinstance(item, dict)
    ]
    rows = [r for r in rows if r["id"] and r["title"] and r["pdfFile"]]

    ok_count = 0
    base = api_url.rstrip("/")
    for row in rows:
        row_id = row["id"]
        payload: dict[str, object] = {"data": row}
        updated = False
        for endpoint in (f"{base}/id/{row_id}", f"{base}/search?id={row_id}"):
            for method in ("PATCH", "PUT"):
                code, _ = sheetdb_request_json(endpoint, method=method, payload=payload)
                if 200 <= code < 300:
                    updated = True
                    break
            if updated:
                break
        if not updated:
            code, _ = sheetdb_request_json(base, method="POST", payload=payload)
            if 200 <= code < 300:
                updated = True
        if updated:
            ok_count += 1

    return ok_count, len(rows)


def to_submission(row: dict[str, str], words_dir: Path) -> Submission:
    docx_path = find_docx_path(row, words_dir)
    body, images = extract_docx_body_and_images(docx_path)
    title_zh = (row.get(HEADER_MAP["title_zh"]) or "").strip()
    authors = (row.get(HEADER_MAP["authors"]) or "").strip()
    affiliation = (row.get(HEADER_MAP["affiliation"]) or "").strip()
    keywords_zh = (row.get(HEADER_MAP["keywords_zh"]) or "").strip()
    abstract_zh = (row.get(HEADER_MAP["abstract_zh"]) or "").strip()
    body = strip_duplicated_front_matter(
        body,
        abstract_zh,
        keywords_zh,
        title_zh,
        authors,
        affiliation,
    )
    return Submission(
        submission_id=(row.get(HEADER_MAP["submission_id"]) or "").strip(),
        title_zh=title_zh,
        title_en=(row.get("英文标题") or "").strip(),
        authors=authors,
        affiliation=affiliation,
        abstract_zh=abstract_zh,
        abstract_en=(row.get("英文摘要") or "").strip(),
        keywords_zh=keywords_zh,
        keywords=split_keywords(keywords_zh),
        keywords_en=(row.get("英文关键词") or "").strip(),
        docx_path=docx_path,
        body_paragraphs=body,
        images=images,
    )


def build_styles() -> dict[str, ParagraphStyle]:
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "TitleZH",
            parent=base["Title"],
            fontName="JournalCJKBold",
            fontSize=21,
            leading=30,
            textColor=BRAND_PRIMARY,
            alignment=1,
        ),
        "meta": ParagraphStyle(
            "Meta",
            parent=base["Normal"],
            fontName="JournalCJK",
            fontSize=10.5,
            leading=16,
            textColor=BRAND_PRIMARY,
            alignment=1,
        ),
        "section": ParagraphStyle(
            "Section",
            parent=base["Heading2"],
            fontName="JournalCJKBold",
            fontSize=13.5,
            leading=20,
            textColor=BRAND_ACCENT,
            spaceBefore=10,
            spaceAfter=4,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName="JournalCJK",
            fontSize=11,
            leading=19,
            textColor=colors.HexColor("#222222"),
            wordWrap="CJK",
            firstLineIndent=2 * 11,
        ),
        "abs": ParagraphStyle(
            "Abstract",
            parent=base["BodyText"],
            fontName="JournalCJK",
            fontSize=10.5,
            leading=17,
            wordWrap="CJK",
            textColor=colors.HexColor("#222222"),
        ),
    }


def draw_first_page_chrome(canvas, doc, submission: Submission, doi: str) -> None:
    canvas.saveState()
    page_width, page_height = A4
    canvas.setFillColor(BRAND_PRIMARY)
    canvas.rect(0, page_height - 30 * mm, page_width, 30 * mm, fill=1, stroke=0)
    canvas.setFillColor(BRAND_ACCENT)
    canvas.rect(0, page_height - 33.5 * mm, page_width, 3.5 * mm, fill=1, stroke=0)
    canvas.setFillColor(colors.white)
    canvas.setFont("JournalCJKBold", 16)
    canvas.drawString(20 * mm, page_height - 19 * mm, "F.U.C.K Journal")
    canvas.setFont("JournalCJK", 10)
    canvas.drawString(20 * mm, page_height - 24.5 * mm, "每一篇，都是DDL的奇迹")
    canvas.setFont("JournalCJK", 9)
    canvas.drawRightString(page_width - 20 * mm, page_height - 18.2 * mm, doi)
    canvas.drawRightString(
        page_width - 20 * mm, page_height - 24.0 * mm, submission.authors
    )

    canvas.setStrokeColor(colors.HexColor("#d9e1ea"))
    canvas.setLineWidth(0.7)
    canvas.line(20 * mm, 25 * mm, page_width - 20 * mm, 25 * mm)

    canvas.setFont("JournalCJK", 9)
    canvas.setFillColor(BRAND_PRIMARY)
    canvas.drawString(20 * mm, 10 * mm, f"F.U.C.K Journal · DOI: {doi}")
    canvas.drawRightString(page_width - 20 * mm, 10 * mm, f"Page {doc.page}")
    canvas.restoreState()


def draw_later_page_chrome(canvas, doc, submission: Submission, doi: str) -> None:
    canvas.saveState()
    page_width, page_height = A4
    canvas.setFillColor(colors.HexColor("#f3f6fa"))
    canvas.rect(0, page_height - 16 * mm, page_width, 16 * mm, fill=1, stroke=0)
    canvas.setStrokeColor(colors.HexColor("#d9e1ea"))
    canvas.setLineWidth(0.7)
    canvas.line(
        20 * mm, page_height - 16 * mm, page_width - 20 * mm, page_height - 16 * mm
    )

    canvas.setFillColor(BRAND_PRIMARY)
    canvas.setFont("JournalCJKBold", 10.5)
    canvas.drawString(20 * mm, page_height - 10.4 * mm, "F.U.C.K Journal")
    canvas.setFont("JournalCJK", 9)
    canvas.drawString(56 * mm, page_height - 10.4 * mm, f"{submission.title_zh[:30]}")
    canvas.drawRightString(page_width - 20 * mm, page_height - 10.4 * mm, doi)

    canvas.setStrokeColor(colors.HexColor("#d9e1ea"))
    canvas.setLineWidth(0.7)
    canvas.line(20 * mm, 22 * mm, page_width - 20 * mm, 22 * mm)

    canvas.setFillColor(BRAND_PRIMARY)
    canvas.setFont("JournalCJK", 9)
    canvas.drawString(20 * mm, 9 * mm, "Fudan Undergraduate Course-worK")
    canvas.drawRightString(page_width - 20 * mm, 9 * mm, f"Page {doc.page}")
    canvas.restoreState()


def render_pdf(submission: Submission, doi: str, out_file: Path) -> None:
    styles = build_styles()
    out_file.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(out_file),
        pagesize=A4,
        leftMargin=25 * mm,
        rightMargin=25 * mm,
        topMargin=40 * mm,
        bottomMargin=18 * mm,
        title=submission.title_zh,
        author=submission.authors,
    )

    story: list[Flowable] = [
        Paragraph(submission.title_zh, styles["title"]),
        Spacer(1, 4 * mm),
        Paragraph(f"作者：{submission.authors}", styles["meta"]),
        Paragraph(f"机构：{submission.affiliation}", styles["meta"]),
        Paragraph(f"DOI: {doi}", styles["meta"]),
        Spacer(1, 6 * mm),
        Paragraph("摘要", styles["section"]),
        Paragraph(submission.abstract_zh or "（未提供）", styles["abs"]),
        Paragraph(f"关键词：{submission.keywords_zh or '（未提供）'}", styles["abs"]),
        Spacer(1, 6 * mm),
        Paragraph("正文", styles["section"]),
    ]

    for line in submission.body_paragraphs:
        if line.startswith("[[IMAGE:"):
            match = re.match(r"\[\[IMAGE:(\d+)\]\]", line)
            if match:
                idx = int(match.group(1))
                if 0 <= idx < len(submission.images):
                    story.append(Spacer(1, 4 * mm))
                    story.append(image_flowable(submission.images[idx], doc.width))
                    story.append(Spacer(1, 4 * mm))
            continue
        if line.startswith("## "):
            story.append(Paragraph(line[3:], styles["section"]))
        else:
            story.append(Paragraph(line, styles["body"]))

    doc.build(
        story,
        onFirstPage=lambda c, d: draw_first_page_chrome(c, d, submission, doi),
        onLaterPages=lambda c, d: draw_later_page_chrome(c, d, submission, doi),
    )


def iter_submissions(
    rows: Iterable[dict[str, str]], words_dir: Path
) -> Iterable[Submission]:
    for row in rows:
        yield to_submission(row, words_dir)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate journal PDFs from Tencent export"
    )
    parser.add_argument(
        "--csv", required=True, type=Path, help="Tencent export CSV path"
    )
    parser.add_argument(
        "--words-dir",
        required=True,
        type=Path,
        help="Directory containing downloaded DOCX files",
    )
    parser.add_argument(
        "--out-dir",
        default=Path("generated_pdfs"),
        type=Path,
        help="Output PDF directory",
    )
    parser.add_argument(
        "--state-file",
        default=Path("tools/doi_state.json"),
        type=Path,
        help="DOI state file",
    )
    parser.add_argument(
        "--doi-year", default=datetime.now().year, type=int, help="DOI year component"
    )
    parser.add_argument(
        "--doi-start", default=4, type=int, help="Starting sequence for new year"
    )
    parser.add_argument(
        "--publish",
        action="store_true",
        help="Copy generated PDF to papers/ and update listing files automatically",
    )
    parser.add_argument(
        "--site-root",
        type=Path,
        default=Path("."),
        help="Website root directory containing paper.html/index.html/papers/",
    )
    parser.add_argument(
        "--sheetdb-api",
        type=str,
        default="",
        help="Optional SheetDB API URL for auto-sync after --publish",
    )
    args = parser.parse_args()

    register_fonts()
    rows = read_rows(args.csv)
    doi_manager = DoiManager(args.state_file, args.doi_year, args.doi_start)

    generated = 0
    for submission in iter_submissions(rows, args.words_dir):
        if not submission.submission_id:
            raise ValueError("submission_id(编号) is required")
        doi = doi_manager.assign(submission.submission_id)
        seq = doi.split(".")[-1]
        generated_name = (
            f"FUCK-{args.doi_year}-{seq}-{slugify(submission.title_zh)}.pdf"
        )
        out_file = args.out_dir / generated_name
        doi_id = doi_to_id(doi)
        published_file = args.site_root / "papers" / f"{doi_id}.pdf"
        render_pdf(submission, doi, out_file)

        rendered = RenderedPaper(
            submission=submission,
            doi=doi,
            doi_id=doi_id,
            generated_pdf=out_file,
            published_pdf=published_file,
        )
        if args.publish:
            publish_paper(args.site_root, rendered)

        generated += 1
        print(f"[OK] {submission.submission_id} -> {out_file}")
        if args.publish:
            print(f"[PUBLISH] {submission.submission_id} -> {published_file}")

    if args.publish and args.sheetdb_api.strip():
        ok, total = sync_catalog_to_sheetdb(args.site_root, args.sheetdb_api.strip())
        print(f"[SHEETDB] Synced {ok}/{total} row(s)")

    print(f"Done. Generated {generated} PDF(s).")


if __name__ == "__main__":
    main()
