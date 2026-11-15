"""Local ground-truth editor: page image + region text + live engine disagreements.

  python scripts/gt_server.py --vol 1932         every image of a volume (printed pages from data/pagemap)
  python scripts/gt_server.py [--pages gt/sample.tsv]   the pages listed in a TSV (vol, index, ...)
  then open http://localhost:8765/  (NER review: http://localhost:8765/ner/, see ner_review.py)

Text comes from, in order: gt/pages/ (verified), gt/wip/ (saved work in progress),
gt/drafts/ (stored draft), else a draft made on the fly from dots-layout, else the text layer.
  Save      -> gt/wip/<vol>/pNNNN.txt
  Verified  -> gt/pages/<vol>/pNNNN.txt (and removes the wip copy); evaluate.py uses these
  Unverify  -> moves the page back to gt/wip/
Per-page notes and dismissed disagreements are kept in gt/wip/<vol>/pNNNN.json.
Binds to 127.0.0.1 only. Standard library only, plus lxml (via regions.py).
"""
import argparse
import csv
import json
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import ner_review
from common import GT, ROOT, img_path
from regions import REGIONS, load, textlayer_tokens, write_gt

WEB = ROOT / "web" / "gt"
ENGINES = ["dots-layout", "lighton"]  # extra readings shown next to each disagreement


PAGES = []  # set in main(): [{vol, index, page, stratum, split}]


def sample():
    return PAGES


def page_list(vol=None, tsv=None):
    test = {(r["vol"], int(r["index"])): r for r in csv.DictReader(open(GT / "sample.tsv"), delimiter="\t")}
    if vol:
        pm = json.loads((ROOT / "data" / "pagemap" / f"{vol}.json").read_text())
        return [{"vol": vol, "index": str(r["index"]), "page": r["page"] or "",
                 "stratum": test.get((vol, r["index"]), {}).get("stratum") or ("" if r["how"] in ("read", "filled") else r["how"]),
                 "split": test.get((vol, r["index"]), {}).get("split", "")} for r in pm]
    return list(csv.DictReader(open(tsv or GT / "sample.tsv"), delimiter="\t"))


def draft_text(vol, idx):
    p = paths(vol, idx)["drafts"]
    if p.exists():
        return p.read_text(encoding="utf-8"), "drafts"
    for engine in ("dots-layout", "textlayer"):
        try:
            return write_gt(load(engine, vol, idx)), engine
        except FileNotFoundError:
            continue
    return "#body\n", "empty"


def paths(vol, idx):
    n = f"p{idx:04d}"
    return {k: GT / k / vol / f"{n}.txt" for k in ("pages", "wip", "drafts")} | {
        "meta": GT / "wip" / vol / f"{n}.json"}


def status(vol, idx):
    p = paths(vol, idx)
    return "verified" if p["pages"].exists() else "wip" if p["wip"].exists() else "draft"


def page_payload(vol, idx):
    p = paths(vol, idx)
    src = status(vol, idx)
    draft, draft_src = draft_text(vol, idx)
    text = p[{"verified": "pages", "wip": "wip"}[src]].read_text(encoding="utf-8") if src != "draft" else draft
    others = {}
    for e in ENGINES:
        try:
            pg = load(e, vol, idx)
            others[e] = [t for r in REGIONS for t in pg[r].split()]
        except FileNotFoundError:
            pass
    from PIL import Image
    with Image.open(img_path(vol, idx)) as im:
        size = im.size
    row = next(r for r in sample() if r["vol"] == vol and int(r["index"]) == idx)
    return {"vol": vol, "index": idx, "page": row["page"], "stratum": row["stratum"], "split": row["split"],
            "status": src, "text": text, "draft": draft, "draft_source": draft_src,
            "meta": json.loads(p["meta"].read_text()) if p["meta"].exists() else {},
            "textlayer": textlayer_tokens(vol, idx), "others": others, "size": size}


def check_text(text):
    regs = re.findall(r"^#(\w+)\s*$", text, flags=re.M)
    bad = [r for r in regs if r not in REGIONS]
    if bad:
        return f"unknown region marker(s): {', '.join('#' + b for b in bad)}"
    if not regs or not text.lstrip().startswith("#"):
        return "text must start with a region marker such as #body"
    for ch, other in (("{", "}"), ("<", ">")):
        if text.count(ch) != text.count(other):
            return f"unbalanced {ch}{other}: {text.count(ch)} vs {text.count(other)}"
    return None


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def route(self):
        m = re.fullmatch(r"/(api/page|img)/(\w+)/(\d+)", self.path.split("?")[0])
        if not m:
            return None, None, None
        vol, idx = m.group(2), int(m.group(3))
        if not any(r["vol"] == vol and int(r["index"]) == idx for r in sample()):
            return m.group(1), None, None
        return m.group(1), vol, idx

    def do_GET(self):
        if ner_review.handle(self, "GET"):       # NER review screens: /ner/, /api/ner/*
            return
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self.send(200, (WEB / "index.html").read_bytes(), "text/html; charset=utf-8")
        if path in ("/app.js", "/style.css"):
            ctype = "text/javascript" if path.endswith(".js") else "text/css"
            return self.send(200, (WEB / path[1:]).read_bytes(), ctype + "; charset=utf-8")
        if path == "/api/pages":
            return self.send(200, [r | {"status": status(r["vol"], int(r["index"]))} for r in sample()])
        kind, vol, idx = self.route()
        if kind and vol is None:
            return self.send(404, {"error": "page not in the page list"})
        if kind == "img":
            return self.send(200, img_path(vol, idx).read_bytes(), "image/jpeg")
        if kind == "api/page":
            return self.send(200, page_payload(vol, idx))
        self.send(404, {"error": "not found"})

    def do_POST(self):
        if not ner_review.handle(self, "POST"):
            self.send(404, {"error": "not found"})

    def do_PUT(self):
        if ner_review.handle(self, "PUT"):
            return
        kind, vol, idx = self.route()
        if kind != "api/page" or vol is None:
            return self.send(404, {"error": "not found"})
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        p = paths(vol, idx)
        action = body.get("action", "save")
        text = body.get("text", "").replace("\r\n", "\n").rstrip() + "\n"
        if action in ("save", "verify"):
            err = check_text(text)
            if err and action == "verify":
                return self.send(400, {"error": err})
            target = p["pages"] if action == "verify" else p["wip"]
            if action == "save" and p["pages"].exists():
                target = p["pages"]  # editing a verified page keeps it verified
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding="utf-8")
            if action == "verify" and p["wip"].exists():
                p["wip"].unlink()
        elif action == "unverify":
            if p["pages"].exists():
                p["wip"].parent.mkdir(parents=True, exist_ok=True)
                p["wip"].write_text(p["pages"].read_text(encoding="utf-8"), encoding="utf-8")
                p["pages"].unlink()
        elif action == "revert":
            for k in ("wip", "pages"):
                if p[k].exists():
                    p[k].unlink()
        if "meta" in body:
            p["meta"].parent.mkdir(parents=True, exist_ok=True)
            p["meta"].write_text(json.dumps(body["meta"], ensure_ascii=False, indent=1))
        self.send(200, {"status": status(vol, idx), "warning": check_text(text) if action == "save" else None})


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--vol", help="list every image of this volume")
    ap.add_argument("--pages", help="TSV of pages (default gt/sample.tsv)")
    a = ap.parse_args()
    PAGES.extend(page_list(a.vol, a.pages))
    print(f"GT editor: http://localhost:{a.port}/  NER review: http://localhost:{a.port}/ner/  (Ctrl+C to stop)")
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()


if __name__ == "__main__":
    main()
