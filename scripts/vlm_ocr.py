"""Page-level OCR with a vision LLM served by vLLM (OpenAI-compatible API).

  python scripts/vlm_ocr.py --mode dots-layout --pages gt/sample.tsv [--workers 4]
  python scripts/vlm_ocr.py --mode lighton --vol 1933a --first 10 --last 20

Modes:
  dots-layout  rednote-hilab/dots.mocr, layout prompt: JSON list of blocks with bbox,
               category (Page-header, Text, Footnote, ...) and Markdown text
  dots-ocr     dots.mocr, plain text prompt
  lighton      lightonai/LightOnOCR-3-0.8B, plain (no prompt)

Output: ocr/<mode>/<vol>/pNNNN.json  {model, mode, seconds, raw, blocks?}.
Existing results are skipped unless --force. Server URL from $VLLM_URL
(default http://localhost:8000/v1). Temperature 0; a reply that hits the token
limit (a loop) is retried once at 0.2.
"""
import argparse
import base64
import csv
import json
import os
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor

from common import OCR, img_path, parse_vols

LAYOUT_PROMPT = """Please output the layout information from the PDF image, including each layout element's bbox, its category, and the corresponding text content within the bbox.

1. Bbox format: [x1, y1, x2, y2]

2. Layout Categories: The possible categories are ['Caption', 'Footnote', 'Formula', 'List-item', 'Page-footer', 'Page-header', 'Picture', 'Section-header', 'Table', 'Text', 'Title'].

3. Text Extraction & Formatting Rules:
    - Picture: For the 'Picture' category, the text field should be omitted.
    - Formula: Format its text as LaTeX.
    - Table: Format its text as HTML.
    - All Others (Text, Title, etc.): Format their text as Markdown.

4. Constraints:
    - The output text must be the original text from the image, with no translation.
    - All layout elements must be sorted according to human reading order.

5. Final Output: The entire output must be a single JSON object.
"""
MODES = {
    "dots-layout": ("rednote-hilab/dots.mocr", "<|img|><|imgpad|><|endofimg|>" + LAYOUT_PROMPT),
    "dots-ocr": ("rednote-hilab/dots.mocr", "<|img|><|imgpad|><|endofimg|>Extract the text content from this image."),
    "lighton": ("lightonai/LightOnOCR-3-0.8B", ""),
}
URL = os.environ.get("VLLM_URL", "http://localhost:8000/v1")


def ask(model, prompt, image, max_tokens, temperature):
    data_url = "data:image/jpeg;base64," + base64.b64encode(image.read_bytes()).decode()
    content = [{"type": "image_url", "image_url": {"url": data_url}}]
    if prompt:
        content.append({"type": "text", "text": prompt})
    body = {"model": model, "temperature": temperature, "max_tokens": max_tokens,
            "messages": [{"role": "user", "content": content}]}
    req = urllib.request.Request(URL + "/chat/completions", json.dumps(body).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as r:
        choice = json.load(r)["choices"][0]
    return choice["message"]["content"], choice.get("finish_reason")


def parse_layout(raw):
    s = raw.strip().removeprefix("```json").removesuffix("```").strip()
    try:
        obj = json.loads(s)
    except json.JSONDecodeError:
        return None
    if isinstance(obj, dict):  # some replies wrap the list
        obj = next((v for v in obj.values() if isinstance(v, list)), [obj])
    return obj


def run(mode, vol, page, max_tokens, force):
    out = OCR / mode / vol / f"p{page:04d}.json"
    if out.exists() and not force:
        return "skip"
    model, prompt = MODES[mode]
    t0 = time.time()
    raw, finish = ask(model, prompt, img_path(vol, page), max_tokens, 0.0)
    retried = False
    if finish == "length":
        raw, finish = ask(model, prompt, img_path(vol, page), max_tokens, 0.2)
        retried = True
    rec = {"model": model, "mode": mode, "vol": vol, "index": page, "seconds": round(time.time() - t0, 1),
           "finish": finish, "retried": retried, "raw": raw}
    if mode == "dots-layout":
        rec["blocks"] = parse_layout(raw)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    return f"{vol} p{page:04d} {rec['seconds']}s {finish}{' (retried)' if retried else ''}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=MODES, required=True)
    ap.add_argument("--pages", help="TSV with vol and index columns (e.g. gt/sample.tsv)")
    ap.add_argument("--vol", default="all")
    ap.add_argument("--first", type=int, default=1)
    ap.add_argument("--last", type=int)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--max-tokens", type=int, default=6000)
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.pages:
        jobs = [(r["vol"], int(r["index"])) for r in csv.DictReader(open(a.pages), delimiter="\t")]
    else:
        jobs = [(v, p) for v in parse_vols(a.vol)
                for p in range(a.first, (a.last or len(list((OCR / 'textlayer' / v).glob('p*.xml')))) + 1)]
    def safe(job):
        try:
            return run(a.mode, *job, a.max_tokens, a.force)
        except Exception as e:  # one bad page (timeout, server hiccup) must not stop the run
            return f"{job[0]} p{job[1]:04d} ERROR {type(e).__name__}: {e}"

    with ThreadPoolExecutor(a.workers) as ex:
        for msg in ex.map(safe, jobs):
            print(msg, flush=True)


if __name__ == "__main__":
    main()
