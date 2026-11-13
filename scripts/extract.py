"""Render PDF pages to JPEG at the scans' native 200 dpi (no resampling).

Pages are rendered (pdftoppm) rather than extracted (pdfimages) because some
pages are composites of several images plus CCITT masks.

  python scripts/extract.py --vol all [--first N --last M] [--jobs 4]

Output: data/img/<vol>/pNNNN.jpg (1-based PDF page index). Existing files are skipped.
"""
import argparse
import subprocess
from concurrent.futures import ThreadPoolExecutor

from common import IMG, img_path, parse_vols, pdf_path

DPI = 200
QUALITY = 92


def n_pages(vol: str) -> int:
    out = subprocess.run(["pdfinfo", str(pdf_path(vol))], capture_output=True, text=True, check=True).stdout
    return int(next(l.split()[-1] for l in out.splitlines() if l.startswith("Pages:")))


def render(vol: str, page: int) -> str:
    out = img_path(vol, page)
    if out.exists():
        return "skip"
    out.parent.mkdir(parents=True, exist_ok=True)
    stem = out.with_suffix("")
    subprocess.run(["pdftoppm", "-r", str(DPI), "-jpeg", "-jpegopt", f"quality={QUALITY}",
                    "-f", str(page), "-l", str(page), "-singlefile", str(pdf_path(vol)), str(stem)], check=True)
    return "ok"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--vol", default="all")
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--last", type=int)
    ap.add_argument("--jobs", type=int, default=4)
    a = ap.parse_args()
    for vol in parse_vols(a.vol):
        last = min(a.last or 10**6, n_pages(vol))
        with ThreadPoolExecutor(a.jobs) as ex:
            res = list(ex.map(lambda p: render(vol, p), range(a.first, last + 1)))
        print(f"{vol}: {res.count('ok')} rendered, {res.count('skip')} skipped -> {IMG / vol}")


if __name__ == "__main__":
    main()
