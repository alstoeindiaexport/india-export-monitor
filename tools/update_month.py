#!/usr/bin/env python3
"""Append one validated month to the India Export Monitor data feed (data.json).

Two ways to supply the figures for a new month:

  1) --figures figures.json   numbers copied verbatim from the official PDF (manual updates)
  2) --quick-pdf <url|path>   the script downloads and parses the PDF itself
                              (needs: pip install pdfplumber requests)

Usage:
  python tools/update_month.py --check
  python tools/update_month.py --month 2026-09 --figures figures.json \
      --quick-pdf "<quick estimates PDF URL>" --release "<press release URL>" \
      --drivers "<Ministry list, verbatim>" --headline-bn 43.81 --headline-prev-bn 34.74 [--dry-run]

figures.json format:
  {"title": "QUICK ESTIMATES FOR SELECTED MAJOR COMMODITIES FOR SEPTEMBER 2026",
   "table": "TRADE: EXPORT (Values in Million USD)",
   "rows": {"Electronic Goods": {"previous": 1.0, "value": 2.0, "growth": 100.0,
                                 "cum_previous": 3.0, "cum_value": 4.0, "cum_growth": 33.33},
            ... the other four sectors and "GRAND TOTAL" ...}}
  previous = same month a year earlier, value = current month, growth = published % change.
  The cum_* fields (April-to-month cumulative columns) are optional but recommended:
  they are checked too, which catches misread digits.

Exit code 0 = written (or check passed); 1 = validation failed, nothing written; 2 = nothing to do.
"""
import argparse, datetime as dt, io, json, re, shutil, sys
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parent.parent
DATA_JSON, DATA_JS = ROOT / "data.json", ROOT / "data.js"
SECTORS = ["Electronic Goods", "Engineering Goods", "Petroleum Products", "Marine Products",
           "Drugs & Pharmaceuticals"]
ROWS = SECTORS + ["GRAND TOTAL"]
MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST",
          "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]
OFFICIAL_HOSTS = ("pib.gov.in", "commerce.gov.in", "dgciskol.gov.in", "nic.in")
UA = {"User-Agent": "Mozilla/5.0 (India Export Monitor updater)"}
TOL = 0.012  # max gap (percentage points) between published and recomputed growth. Rounding alone
             # stays under ~0.008 (worst seen in 17 months: 0.0052); a misread digit usually exceeds it.


def fail(msg):
    print(f"VALIDATION FAILED: {msg}\nNothing was written.")
    sys.exit(1)


def official(url):
    try:
        u = urlparse(url)
    except ValueError:
        return False
    host = (u.hostname or "").lower()
    return u.scheme == "https" and any(host == h or host.endswith("." + h) for h in OFFICIAL_HOSTS)


def clean_text(s):
    """Collapse whitespace and remove em/en dashes (house style for the website)."""
    s = re.sub(r"\s+", " ", s or "").strip()
    s = re.sub(r"\s*[\u2013\u2014]\s*", ", ", s)
    return s.rstrip(" .;,")


def load_bytes(src):
    if re.match(r"https?://", src):
        import requests
        r = requests.get(src, headers=UA, timeout=60)
        r.raise_for_status()
        return r.content
    return Path(src).read_bytes()


def pdf_pages(raw):
    import pdfplumber
    with pdfplumber.open(io.BytesIO(raw)) as pdf:
        return [(p.extract_text() or "") for p in pdf.pages]


def check_row(name, prev, value, growth, label="month"):
    if not all(isinstance(x, (int, float)) for x in (prev, value, growth)):
        fail(f"'{name}' ({label}): values must be numbers")
    if prev <= 0 or value <= 0:
        fail(f"'{name}' ({label}): non-positive value (previous={prev}, value={value})")
    calc = (value / prev - 1) * 100
    if abs(calc - growth) > TOL:
        fail(f"'{name}' ({label}): published growth {growth} != recomputed {calc:.2f} "
             f"(previous={prev}, value={value}); a digit or column was probably misread")


def finish_rows(out):
    five = sum(out[s]["value"] for s in SECTORS)
    if not five < out["GRAND TOTAL"]["value"]:
        fail("five-sector sum is not below the grand total")
    five_prev = sum(out[s]["previous"] for s in SECTORS)
    if not five_prev < out["GRAND TOTAL"]["previous"]:
        fail("five-sector year-ago sum is not below the year-ago grand total")
    return out


def parse_figures(path, key):
    year, mon = key.split("-")
    mname = MONTHS[int(mon) - 1]
    try:
        fig = json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        fail(f"cannot read figures file: {e}")
    title = re.sub(r"\s+", " ", str(fig.get("title", ""))).upper()
    if f"MAJOR COMMODITIES FOR {mname} {year}" not in title:
        fail(f"figures title does not say 'MAJOR COMMODITIES FOR {mname} {year}': {fig.get('title')!r}")
    if "COUNTRIES" in title:
        fail("this is the SELECTED COUNTRIES file, not the commodities file")
    table = re.sub(r"\s+", " ", str(fig.get("table", ""))).upper()
    if "EXPORT" not in table or "MILLION USD" not in table:
        fail(f"table must be 'TRADE: EXPORT (Values in Million USD)', got {fig.get('table')!r}")
    rows = fig.get("rows") or {}
    out = {}
    for name in ROWS:
        r = rows.get(name)
        if not isinstance(r, dict):
            fail(f"row '{name}' missing from figures")
        prev, value, growth = r.get("previous"), r.get("value"), r.get("growth")
        check_row(name, prev, value, growth)
        if all(k in r for k in ("cum_previous", "cum_value", "cum_growth")):
            cp, cv, cg = r["cum_previous"], r["cum_value"], r["cum_growth"]
            check_row(name, cp, cv, cg, "cumulative")
            if mname == "APRIL":
                if abs(cp - prev) > 0.011 or abs(cv - value) > 0.011:
                    fail(f"'{name}': April cumulative must equal the month")
            elif cv <= value or cp <= prev:
                fail(f"'{name}': cumulative columns must exceed the single month")
        out[name] = {"value": float(value), "previous": float(prev), "growth": float(growth)}
    return finish_rows(out)


def page_head(t):
    return re.sub(r"[\s\-]+", " ", t[:600]).upper().replace(" :", ":").replace(": ", ":")


def parse_quick(raw, key):
    year, mon = key.split("-")
    mname = MONTHS[int(mon) - 1]
    pages = pdf_pages(raw)

    def our_table(h):
        return f"MAJOR COMMODITIES FOR {mname} {year}" in h and "TRADE:EXPORT" in h and "MILLION USD" in h
    start = next((i for i, t in enumerate(pages) if our_table(page_head(t))), None)
    if start is None:
        fail(f"no 'QUICK ESTIMATES ... FOR {mname} {year} / TRADE: EXPORT / Million USD' table found in the PDF")
    # The table can run onto the next pages. It ends at its notes, or where a different table starts.
    page = pages[start]
    for t in pages[start + 1:]:
        h = page_head(t)
        if "Note 1:" in page or (("QUICK ESTIMATES" in h or "TRADE:" in h) and not our_table(h)):
            break
        page += "\n" + t
    page = page.split("Note 1:")[0]
    out = {}
    for name in ROWS:
        pat = r"\s+".join(re.escape(w) for w in name.split())
        m = re.search(pat, page, re.I)
        if not m:
            fail(f"row '{name}' not found")
        n = 3 if mname == "APRIL" else 6
        tokens = re.findall(r"-?\d+(?:\.\d+)?", page[m.end():])[:n]
        if len(tokens) < n:
            fail(f"row '{name}' has too few numbers: {tokens}")
        # Every figure in these tables has two decimals. Fewer means a cell wrapped onto the next line and
        # the number was cut, which the growth checks alone might not catch.
        if not all(re.fullmatch(r"-?\d+\.\d\d", x) for x in tokens):
            fail(f"row '{name}': numbers look cut off ({' '.join(tokens)}); the table cells wrap in this PDF, "
                 "so add this month with --figures instead")
        nums = [float(x) for x in tokens]
        prev, value, growth = (nums[0], nums[1], nums[2]) if n == 3 else (nums[0], nums[2], nums[4])
        check_row(name, prev, value, growth)
        if n == 6:
            check_row(name, nums[1], nums[3], nums[5], "cumulative")
        out[name] = {"value": value, "previous": prev, "growth": growth}
    return finish_rows(out)


def release_text(src):
    raw = load_bytes(src)
    if raw[:4] == b"%PDF":
        return " ".join(pdf_pages(raw))
    html = raw.decode("utf-8", "ignore")
    html = re.sub(r"(?is)<(script|style).*?</\1>", " ", html)
    return re.sub(r"<[^>]+>", " ", html)


def extract_drivers(text):
    t = re.sub(r"\s+", " ", text).replace("&amp;", "&")
    m = re.search(r"Major drivers of merchandise exports? growth in [A-Za-z]+,? \d{4} include (.+?)\.\s*"
                  r"[A-Z][A-Za-z,&/ .()-]{2,90}? exports? increased", t)
    return m.group(1).strip() if m else ""


def month_label(key):
    y, m = key.split("-")
    return f"{MONTHS[int(m) - 1].title()} {y}"


def next_key(key):
    y, m = map(int, key.split("-"))
    return f"{y + (m == 12)}-{m % 12 + 1:02d}"


def check_series(data):
    if not isinstance(data, list) or not data:
        fail("data.json must be a non-empty list")
    for i, d in enumerate(data):
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", str(d.get("key", ""))):
            fail(f"record {i}: bad key {d.get('key')!r}")
        if d.get("month") != month_label(d["key"]):
            fail(f"{d['key']}: month label should be {month_label(d['key'])!r}")
        for name in ROWS:
            s = d["sectors"][name]
            if abs((s["value"] / s["previous"] - 1) * 100 - s["growth"]) > TOL:
                fail(f"{d['month']} {name}: stored growth inconsistent")
        for f in ("source", "release"):
            if not official(d.get(f, "")):
                fail(f"{d['month']}: {f} URL is not an official https government link: {d.get(f)!r}")
        if re.search("[\u2013\u2014]", d.get("officialDrivers", "")):
            fail(f"{d['month']}: officialDrivers contains an em or en dash")
        if i and next_key(data[i - 1]["key"]) != d["key"]:
            fail(f"gap between {data[i-1]['key']} and {d['key']}")


def write(data):
    shutil.copy(DATA_JSON, DATA_JSON.with_suffix(f".json.bak-{data[-2]['key']}"))
    DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    if DATA_JS.exists():  # standalone dashboard copy, if present
        meta = {"updated": dt.date.today().isoformat(), "latest": data[-1]["key"], "months": len(data)}
        DATA_JS.write_text("window.EXPORT_DATA=" + json.dumps(data, ensure_ascii=False) + ";\n"
                           "window.EXPORT_META=" + json.dumps(meta) + ";\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--month"); ap.add_argument("--quick-pdf"); ap.add_argument("--release")
    ap.add_argument("--figures", help="JSON file with figures copied verbatim from the PDF")
    ap.add_argument("--drivers", default=None)
    ap.add_argument("--headline-bn", type=float, help="press release: merchandise exports this month, US$ bn")
    ap.add_argument("--headline-prev-bn", type=float, help="press release: same month last year, US$ bn")
    ap.add_argument("--headline-growth", type=float, help="press release: growth %% for the month, if stated")
    ap.add_argument("--dry-run", action="store_true"); ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    data = json.loads(DATA_JSON.read_text(encoding="utf-8"))
    check_series(data)
    if a.check:
        print(f"OK: {len(data)} months, {data[0]['month']} to {data[-1]['month']}, all checks pass.")
        print(f"NEXT MONTH TO ADD: {next_key(data[-1]['key'])}")
        return
    if not (a.month and a.quick_pdf and a.release):
        ap.error("--month, --quick-pdf and --release are required (or use --check)")
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", a.month):
        fail(f"--month must look like 2026-09, got {a.month!r}")
    if any(d["key"] == a.month for d in data):
        print(f"NOTHING TO DO: {a.month} is already in the dashboard.")
        sys.exit(2)
    if a.month != next_key(data[-1]["key"]):
        fail(f"expected next month {next_key(data[-1]['key'])}, got {a.month} (months must be added in order)")
    for label, url in (("--quick-pdf", a.quick_pdf), ("--release", a.release)):
        if not official(url):
            fail(f"{label} must be an https link on an official host {OFFICIAL_HOSTS}: {url!r}")
    if a.figures:
        sectors = parse_figures(a.figures, a.month)
    else:
        sectors = parse_quick(load_bytes(a.quick_pdf), a.month)
    t = sectors["GRAND TOTAL"]
    if a.headline_bn is not None and abs(t["value"] / 1000 - a.headline_bn) > 0.006:
        fail(f"GRAND TOTAL {t['value']} mn does not match the press release headline US$ {a.headline_bn} bn "
             "(wrong file or misread number)")
    if a.headline_prev_bn is not None and abs(t["previous"] / 1000 - a.headline_prev_bn) > 0.006:
        fail(f"year-ago GRAND TOTAL {t['previous']} mn does not match the press release US$ {a.headline_prev_bn} bn")
    if a.headline_growth is not None and abs(t["growth"] - a.headline_growth) > 0.011:
        fail(f"GRAND TOTAL growth {t['growth']}% does not match the press release {a.headline_growth}%")
    # Revision sanity check against the value stored for the same month last year (warning only).
    y, m = a.month.split("-")
    ly = next((d for d in data if d["key"] == f"{int(y)-1}-{m}"), None)
    warnings = []
    if ly:
        for name in ROWS:
            old, base = ly["sectors"][name]["value"], sectors[name]["previous"]
            if abs(base / old - 1) > 0.10:
                warnings.append(f"{name}: year-ago base {base} differs >10% from stored {old} (large revision, verify)")
    drivers = a.drivers
    if drivers is None:
        try:
            drivers = extract_drivers(release_text(a.release))
        except Exception as e:  # noqa: BLE001  network or parse problem is not fatal
            drivers = ""
            warnings.append(f"could not read release for drivers: {e}")
        if not drivers:
            warnings.append("Ministry 'major drivers' sentence not found; pass --drivers \"...\" to add it")
    drivers = clean_text(drivers)
    if a.headline_bn is None:
        warnings.append("press release headline not cross-checked (pass --headline-bn)")
    rec = {"month": month_label(a.month), "key": a.month, "source": a.quick_pdf,
           "sectors": sectors, "officialDrivers": drivers, "release": a.release}
    print(f"{rec['month']}: total US$ {t['value']:,.2f} mn ({t['growth']:+.2f}% YoY), "
          f"year-ago US$ {t['previous']:,.2f} mn")
    for s in SECTORS:
        v = sectors[s]
        print(f"  {s:<24} {v['value']:>10,.2f}  prev {v['previous']:>10,.2f}  {v['growth']:+7.2f}%")
    print(f"  Ministry highlights: {drivers or '(none)'}")
    for w in warnings:
        print("WARNING:", w)
    if a.dry_run:
        print("DRY RUN: nothing written.")
        return
    data.append(rec)
    check_series(data)
    write(data)
    print(f"WRITTEN: data now covers {data[0]['month']} to {data[-1]['month']} ({len(data)} months).")


if __name__ == "__main__":
    main()
