#!/usr/bin/env python3
"""Find and add newly published months to data.json. Runs in GitHub Actions (see .github/workflows).

  python tools/auto_update.py              add every newly published month, oldest first
  python tools/auto_update.py --verify 2026-08
                                           re-read an existing month from the official PDF and compare;
                                           writes nothing

Where the figures come from (official sources only):
  1. PIB "All releases" list for the Ministry of Commerce & Industry, filtered to the month after the data month.
     The monthly trade release gives the press release link, the "major drivers" sentence, the headline
     merchandise figure and the link to the DGCI&S quick-estimates PDF.
  2. If the PIB release cannot be found, the DGCI&S Quick Estimates page (export, USD, commodities PDF).
Every number is then checked by tools/update_month.py before anything is written.

Exit codes: 0 = done or nothing new yet; 1 = something needs a human (validation failed, or the release
is still missing after the 25th of the release month).
"""
import argparse, datetime as dt, json, os, re, sys, time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urljoin

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_month as um  # noqa: E402

PIB_LIST = "https://www.pib.gov.in/allRel.aspx?reg=3&lang=1"
PIB_RELEASE = "https://www.pib.gov.in/PressReleasePage.aspx?PRID={}"
DGCIS_LIST = "https://www.dgciskol.gov.in/Quick_Estimate.aspx"
COMMERCE_MINISTRY = "16"  # "Ministry of Commerce & Industry" in the PIB ministry filter
MONTHS = [m.title() for m in um.MONTHS]
S = requests.Session()
S.headers.update({"User-Agent": "Mozilla/5.0 (compatible; IndiaExportMonitor/1.0; "
                                "+https://alstoeindiaexports.com/india-export-monitor/)"})


def log(*a):
    print(*a, flush=True)


def http(method, url, **kw):
    last = None
    for attempt in range(4):
        try:
            r = S.request(method, url, timeout=60, **kw)
            if r.status_code < 500:
                return r
            last = f"HTTP {r.status_code}"
        except requests.RequestException as e:
            last = str(e)
        time.sleep(5 * (attempt + 1))
    raise RuntimeError(f"{method} {url} failed: {last}")


class FormParser(HTMLParser):
    """Collects what a browser would submit for the first form, plus every link and its text."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.fields, self.links = {}, []
        self._select, self._first, self._chosen = None, None, False
        self._a, self._text = None, []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "input" and a.get("name") and a.get("type", "text").lower() in ("hidden", "text"):
            self.fields[a["name"]] = a.get("value") or ""
        elif tag == "select" and a.get("name"):
            self._select, self._first, self._chosen = a["name"], None, False
        elif tag == "option" and self._select:
            v = a.get("value", "")
            if self._first is None:
                self._first = v
            if "selected" in a:
                self.fields[self._select], self._chosen = v, True
        elif tag == "a" and a.get("href"):
            self._a, self._text = a["href"], []

    def handle_endtag(self, tag):
        if tag == "select" and self._select:
            if not self._chosen and self._first is not None:
                self.fields[self._select] = self._first
            self._select = None
        elif tag == "a" and self._a is not None:
            self.links.append((self._a, re.sub(r"\s+", " ", "".join(self._text)).strip()))
            self._a = None

    def handle_data(self, data):
        if self._a is not None:
            self._text.append(data)


def parse(html):
    p = FormParser()
    p.feed(html)
    return p


def text_of(html):
    html = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", html)
    html = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", html)
    txt = re.sub(r"<[^>]+>", " ", html)
    import html as h
    return re.sub(r"[ \t\r\f\v]+", " ", h.unescape(txt))


def release_month(key):
    y, m = map(int, key.split("-"))
    return (y + (m == 12), m % 12 + 1)


def find_pib_release(key):
    """Return dict(release, quick_pdf, drivers, headline_bn, headline_prev_bn) or None."""
    y, m = map(int, key.split("-"))
    ry, rm = release_month(key)
    month = MONTHS[m - 1]
    page = http("GET", PIB_LIST)
    form = parse(page.text).fields
    form.update({
        "__EVENTTARGET": "ctl00$ContentPlaceHolder1$ddlMinistry", "__EVENTARGUMENT": "",
        "ctl00$ContentPlaceHolder1$ddlMinistry": COMMERCE_MINISTRY,
        "ctl00$ContentPlaceHolder1$ddlday": "0",
        "ctl00$ContentPlaceHolder1$ddlMonth": str(rm),
        "ctl00$ContentPlaceHolder1$ddlYear": str(ry),
    })
    res = http("POST", PIB_LIST, data=form)
    links = parse(res.text).links
    log(f"PIB: {len(links)} links listed for Commerce & Industry, {MONTHS[rm - 1]} {ry}")
    cands = []
    for href, title in links:
        mm = re.search(r"PRID=(\d+)", href)
        t = title.lower()
        if mm and "export" in t and month.lower() in t and str(y) in t and mm.group(1) not in [c[0] for c in cands]:
            cands.append((mm.group(1), title))
    for prid, title in cands[:6]:
        url = PIB_RELEASE.format(prid)
        r = http("GET", url)
        txt = re.sub(r"\s+", " ", text_of(r.text))
        head = re.search(rf"Merchandise exports (?:during|in|for) {month},? {y} (?:were|was|stood at|"
                         rf"(?:is|are) estimated at) US\$ ?([\d.]+) Billion(?:,? (?:as )?compared to US\$ ?([\d.]+) Billion)?",
                         txt, re.I)
        if not head:
            continue
        drv = re.search(rf"Major drivers of merchandise exports? growth in {month},? {y} include (.+?)\.\s*(?=[A-Z])",
                        txt, re.I)
        pdf = None
        for href, label in parse(r.text).links:
            if "quick estimate" in label.lower() and href.lower().split("?")[0].endswith(".pdf"):
                pdf = urljoin(url, href)
                break
        log(f"PIB release found: PRID {prid}: {title[:110]}")
        return {"release": url, "quick_pdf": pdf, "drivers": drv.group(1).strip() if drv else "",
                "headline_bn": float(head.group(1)),
                "headline_prev_bn": float(head.group(2)) if head.group(2) else None}
    return None


def find_dgcis_pdf(key):
    y, m = map(int, key.split("-"))
    month = MONTHS[m - 1]
    page = http("GET", DGCIS_LIST)
    pat = re.compile(rf"major commodities for export\s*\(\s*usd\s*\)\s*{month}\s*-?\s*({y}|{str(y)[2:]})\b", re.I)
    # The description and the PDF icon sit in different cells of the same table row.
    for row in re.findall(r"(?is)<tr\b.*?</tr>", page.text):
        label = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", row))
        if not pat.search(label):
            continue
        for href in re.findall(r'(?i)href\s*=\s*["\']([^"\']+\.pdf)["\']', row):
            url = urljoin(DGCIS_LIST, href.strip()).replace(" ", "%20")
            log(f"DGCI&S PDF found: {label.strip()[:100]}")
            return url
    return None


def records_match(a, b):
    return all(abs(a[n][k] - b[n][k]) < 0.0051 for n in um.ROWS for k in ("value", "previous", "growth"))


def process(key, data, verify=False):
    """Returns 'added', 'verified', or 'missing'."""
    info = find_pib_release(key)
    pdf = info["quick_pdf"] if info and info.get("quick_pdf") else None
    if not pdf:
        pdf = find_dgcis_pdf(key)
    if not pdf:
        return "missing"
    raw = http("GET", pdf).content
    if raw[:4] != b"%PDF":
        um.fail(f"{pdf} did not return a PDF")
    sectors = um.parse_quick(raw, key)
    t = sectors["GRAND TOTAL"]
    if info:
        prev_bn = info["headline_prev_bn"]
        if abs(t["value"] / 1000 - info["headline_bn"]) > 0.006 or (prev_bn is not None and abs(t["previous"] / 1000 - prev_bn) > 0.006):
            um.fail(f"PDF total {t['value']} / {t['previous']} does not match the press release headline "
                    f"US$ {info['headline_bn']} bn / {info['headline_prev_bn']} bn")
    if verify:
        stored = next(d for d in data if d["key"] == key)["sectors"]
        ok = records_match(stored, sectors)
        log(f"VERIFY {key}: {'MATCHES' if ok else 'DIFFERS FROM'} the official PDF ({pdf})")
        if not ok:
            for n in um.ROWS:
                log(f"  {n}: stored {stored[n]} pdf {sectors[n]}")
            sys.exit(1)
        return "verified"
    release = info["release"] if info else DGCIS_LIST
    if not um.official(pdf) or not um.official(release):
        um.fail(f"non-official link: {pdf} / {release}")
    drivers = um.clean_text(info["drivers"]) if info else ""
    ly = next((d for d in data if d["key"] == f"{int(key[:4]) - 1}{key[4:]}"), None)
    if ly:
        for n in um.ROWS:
            old, base = ly["sectors"][n]["value"], sectors[n]["previous"]
            if abs(base / old - 1) > 0.10:
                log(f"WARNING: {n}: year-ago base {base} differs >10% from stored {old}")
    rec = {"month": um.month_label(key), "key": key, "source": pdf, "sectors": sectors,
           "officialDrivers": drivers, "release": release}
    data.append(rec)
    um.check_series(data)
    log(f"ADDED {rec['month']}: total US$ {t['value'] / 1000:.2f} bn ({t['growth']:+.2f}% YoY)")
    for n in um.SECTORS:
        v = sectors[n]
        log(f"  {n:<24} {v['value']:>10,.2f}  ({v['growth']:+.2f}%)")
    log(f"  Ministry highlights: {drivers or '(not found)'}")
    return "added"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", help="YYYY-MM of an existing month to re-check against its official PDF")
    a = ap.parse_args()
    verify = (a.verify or os.environ.get("VERIFY_MONTH", "")).strip()
    data = json.loads(um.DATA_JSON.read_text(encoding="utf-8"))
    um.check_series(data)
    if verify:
        if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", verify) or not any(d["key"] == verify for d in data):
            um.fail(f"--verify needs a month that is already in data.json, got {verify!r}")
        if process(verify, data, verify=True) == "missing":
            um.fail(f"could not find the official documents for {verify}")
        return
    added = []
    while True:
        key = um.next_key(data[-1]["key"])
        status = process(key, data)
        if status != "added":
            ry, rm = release_month(key)
            today = dt.date.today()
            log(f"NO NEW RELEASE YET for {um.month_label(key)} (expected around 15 {MONTHS[rm - 1]} {ry}).")
            if not added and today >= dt.date(ry, rm, 25):
                log("The release is overdue or could not be found. Please check PIB and DGCI&S manually.")
                sys.exit(1)
            break
        added.append(um.month_label(key))
    if added:
        um.DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
        msg = "Add " + ", ".join(added) + " quick estimates"
        out = os.environ.get("COMMIT_MSG_FILE")
        if out:
            Path(out).write_text(msg + "\n", encoding="utf-8")
        log("WRITTEN:", msg)


if __name__ == "__main__":
    main()
