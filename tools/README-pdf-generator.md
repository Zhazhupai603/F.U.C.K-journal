# Journal PDF Generator

This tool turns Tencent questionnaire export + downloaded DOCX files into unified journal PDFs.

## Input
- Tencent export CSV (current verified sample: `25846300_202603030134363050.csv`)
- Local DOCX directory (current path: `F.U.C.K-journal/words/`)

## How it works
1. Reads CSV (`utf-8-sig` and `gb18030` supported)
2. Parses attachment cell format `=Hyperlink("url"，"display")`
3. Resolves DOCX from local `words` directory by attachment display name
4. Extracts DOCX content
5. Assigns DOI with persistent map (`submission_id -> DOI`)
6. Renders A4 single-column branded PDF

## Run

```bash
python tools/pdf_generator.py \
  --csv "/home/eagle/pig/国行供应链/25846300_202603030134363050.csv" \
  --words-dir "/home/eagle/pig/国行供应链/F.U.C.K-journal/words" \
  --out-dir "/home/eagle/pig/国行供应链/F.U.C.K-journal/generated_pdfs" \
  --state-file "/home/eagle/pig/国行供应链/F.U.C.K-journal/tools/doi_state.json" \
  --doi-year 2026 \
  --doi-start 4
```

## One-command generate + publish

```bash
python tools/pdf_generator.py \
  --csv "/home/eagle/pig/国行供应链/25846300_202603030134363050.csv" \
  --words-dir "/home/eagle/pig/国行供应链/F.U.C.K-journal/words" \
  --out-dir "/home/eagle/pig/国行供应链/F.U.C.K-journal/generated_pdfs" \
  --state-file "/home/eagle/pig/国行供应链/F.U.C.K-journal/tools/doi_state.json" \
  --doi-year 2026 \
  --doi-start 4 \
  --publish \
  --site-root "/home/eagle/pig/国行供应链/F.U.C.K-journal"
```

This publish mode will:
- copy PDF into `papers/{DOI-with-dash}.pdf`
- append/update catalog `papers/papers.json`
- append the paper entry in `paper.html`
- append the homepage card in `index.html`
- write detail metadata (`authors/date/volume/abstract/method/conclusion/keywords`) into `papers/papers.json` for detail page sync

## Output
- PDF files in `generated_pdfs/`
- DOI state map in `tools/doi_state.json`

## Notes
- Chinese body + English abstract is supported.
- If attachment URL cannot be downloaded directly due to login, keep the DOCX in `words/` and preserve filename consistency.
