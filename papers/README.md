# Paper PDF Storage

Put uploaded final paper PDFs in this folder.

## Naming rule

- Keep filename aligned with the paper id in `paper-detail.html`.
- Example:
  - `10.FUCK-2026.001.pdf`
  - `10.FUCK-2026.002.pdf`
  - `10.FUCK-2026.003.pdf`

## How download works

1. `Download PDF` first checks `papers/<pdfFile>`.
2. If the file exists, it downloads the original uploaded PDF.
3. If missing, it falls back to generated PDF from page content.

## Auto-load to homepage carousel

The homepage list and detail page read `papers/papers.json` automatically.
The homepage featured carousel reads `papers/featured.json`.

After you put new PDF files in this folder, run:

```bash
python scripts/generate_papers_catalog.py
```

Then refresh the website. The new papers will show up in:

- homepage scrolling featured cards
- homepage paper list
- detail page lookup/download

To control which papers appear in the featured carousel,
edit `papers/featured.json` (use paper IDs).

## Avoid manual upload every time

You do NOT need to edit SheetDB rows manually each time.

Use one command to sync local catalog to SheetDB:

```bash
python scripts/sync_papers_to_sheetdb.py \
  --api "https://sheetdb.io/api/v1/ev8cjfow1i90w" \
  --source "papers/papers.json"
```

Typical workflow:

1. Put new PDFs in `papers/`
2. Update `papers/papers.json` (or generate it)
3. Run the sync command above
4. Refresh the site

## Optional custom metadata

You can still edit `papers/papers.json` to give better titles:

- `title`: card and list display text
- `coverImage`: custom cover path for homepage/detail
