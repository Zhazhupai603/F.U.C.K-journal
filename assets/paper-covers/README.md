# Paper Cover Images

Put paper cover images in this folder.

## Suggested naming

- `10.FUCK-2026.001.jpg`
- `10.FUCK-2026.002.jpg`
- `10.FUCK-2026.003.jpg`

## Where these are used

1. Homepage featured scrolling gallery (`index.html`)
2. Paper detail hero cover (`paper-detail.html`)

If an image file is missing, the page auto-falls back to a generated placeholder.

## How to match with PDFs

- The default rule maps `papers/<id>.pdf` to `assets/paper-covers/<id>.jpg`.
- You can override per paper in `papers/papers.json` via `coverImage`.
