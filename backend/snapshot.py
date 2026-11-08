"""Adds today's spice prices to web/data/price-history.json. Run by GitHub Actions every 6 hours.

Official Spices Board prices need no key. AI-found prices are added only when GEMINI_API_KEY is set
(as a GitHub Actions secret); without it this step is skipped quietly.
"""
import json
import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import markets  # noqa: E402

OUT = HERE.parent / "web" / "data" / "price-history.json"
KEEP = 500


def key_of(r):
    return re.sub(r"[^a-z0-9]+", "-", f"{r['spice']} {r.get('market','')} {r.get('grade','')} {r['unit']}".lower()).strip("-")


def main():
    hist = json.loads(OUT.read_text("utf-8")) if OUT.exists() else {"series": {}, "meta": {}}
    rows, notes = [], []
    try:
        rows += markets.fetch_spices_board()
    except Exception as e:
        notes.append(f"Spices Board: {e}")
    if os.getenv("GEMINI_API_KEY"):
        try:
            import server  # noqa: E402  (does not start the web server)
            ai = server._ai_prices()
            rows += ai["india"] + ai["world"]
        except Exception as e:
            notes.append(f"AI prices: {e}")
    added = 0
    for r in rows:
        if not r.get("date"):
            continue
        k = key_of(r)
        s = hist["series"].setdefault(k, [])
        hist["meta"][k] = {x: r.get(x, "") for x in ("spice", "market", "grade", "unit", "source")} | {"official": bool(r.get("official"))}
        if any(p[0] == r["date"] for p in s):
            continue
        s.append([r["date"], r["price"]])
        s.sort()
        del s[:-KEEP]
        added += 1
    if added:
        hist["updated"] = markets.now_iso()
        OUT.parent.mkdir(parents=True, exist_ok=True)
        OUT.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")), "utf-8")
    print(f"added {added} points from {len(rows)} prices", *notes, sep="\n")


if __name__ == "__main__":
    main()
