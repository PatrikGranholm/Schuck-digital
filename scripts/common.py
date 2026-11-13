"""Shared paths and the volume registry."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PDF = ROOT / "data" / "pdf"
IMG = ROOT / "data" / "img"
OCR = ROOT / "ocr"
GT = ROOT / "gt"
REPORT = ROOT / "report"

# volume id = PDF stem suffix; part = Roman numeral printed on the title page
VOLUMES = {
    "1932": {"part": "I", "year": 1932},
    "1933a": {"part": "II", "year": 1933},
    "1933b": {"part": "III", "year": 1933},
    "1935": {"part": "IV", "year": 1935},
    "1936": {"part": "V", "year": 1936},
    "1943a": {"part": "VI", "year": 1943},
    "1943b": {"part": "VII", "year": 1943},
    "1944": {"part": "VIII", "year": 1944},
}
LIBRIS = "https://libris.kb.se/cvn7fkgp3mft1s5"


def pdf_path(vol: str) -> Path:
    return PDF / f"Schück_{vol}.pdf"


def img_path(vol: str, page: int) -> Path:
    """page = 1-based PDF page index (not the printed page number)."""
    return IMG / vol / f"p{page:04d}.jpg"


def parse_vols(spec: str) -> list[str]:
    if spec == "all":
        return list(VOLUMES)
    vols = spec.split(",")
    bad = [v for v in vols if v not in VOLUMES]
    if bad:
        raise SystemExit(f"unknown volume(s): {bad}; known: {list(VOLUMES)}")
    return vols
