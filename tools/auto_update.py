#!/usr/bin/env python3
"""Find and add newly published months to data.json. Runs in GitHub Actions (see .github/workflows).

  python tools/auto_update.py                    add every newly published month, oldest first
  python tools/auto_update.py --verify 2026-08   re-read a stored month (or "all") from the PDF it cites
                                                 and compare; writes nothing
  python tools/auto_update.py --annotate 2026-09 --drivers "<Ministry list>" --release "<release link>"
                                                 add the Ministry's highlights to a stored month

Where the figures come from (official sources only, tried in this order):
  1. PIB "All releases" list for the Ministry of Commerce & Industry, filtered to the month after the data month.
     The monthly trade release gives the press release link, the "major drivers" sentence, the headline
     merchandise figure and the link to the DGCI&S quick-estimates PDF.
  2. The Department of Commerce release list on commerce.gov.in: the "Monthly Press Release on India's Foreign
     Trade" PDF (same text as the PIB release) and the "Quick Estimates for selected Major Commodities" PDF.
  3. The DGCI&S Quick Estimates page (export, USD, commodities PDF), for the figures only.
PIB and commerce.gov.in refuse requests from GitHub's servers (HTTP 403), so on GitHub the figures come from
DGCI&S and the Ministry's highlights can be added afterwards with --annotate (or the workflow form).
Every number is checked by tools/update_month.py before anything is written.

Exit codes: 0 = done or nothing new yet; 1 = something needs a human (validation failed, or the release
is still missing after the 25th of the release month).
"""
import argparse, datetime as dt, html, json, os, re, sys, time
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import quote, unquote, urljoin

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_month as um  # noqa: E402

PIB_LIST = "https://www.pib.gov.in/allRel.aspx?reg=3&lang=1"
PIB_RELEASE = "https://www.pib.gov.in/PressReleasePage.aspx?PRID={}"
COMMERCE = "https://www.commerce.gov.in/"
COMMERCE_API = COMMERCE + "ministryofcommerce/api/v1/press-release-pib"
COMMERCE_LIST = COMMERCE + "#/documents/press-releases/data-related-releases"  # link when no release was read
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


def parse(page):
    p = FormParser()
    p.feed(page)
    return p


def text_of(page):
    page = re.sub(r"(?is)<(script|style)\b.*?</\1>", " ", page)
    page = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</li>|</tr>|</h\d>", "\n", page)
    return re.sub(r"[ \t\r\f\v]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", page)))


def describe(r):
    """Status and page title of a response that was not what we expected, for the log."""
    m = re.search(r"(?is)<title[^>]*>(.*?)</title>", r.text or "")
    return f"HTTP {r.status_code}, " + (re.sub(r"\s+", " ", m.group(1)).strip()[:60] if m else "no page title")


def release_month(key):
    y, m = map(int, key.split("-"))
    return (y + (m == 12), m % 12 + 1)


def read_release(txt, key):
    """Headline figures and the "major drivers" list from the text of the monthly trade release, or None."""
    y, m = map(int, key.split("-"))
    month = MONTHS[m - 1]
    txt = re.sub(r"\s+", " ", txt)
    head = re.search(rf"Merchandise exports (?:during|in|for) {month},? {y} (?:were|was|stood at|"
                     rf"(?:is|are) estimated at) US ?\$ ?([\d.]+) Billion(?:,? (?:as )?compared to "
                     rf"US ?\$ ?([\d.]+) Billion)?", txt, re.I)
    if not head:
        return None
    # The list ends at the first full stop that is followed by a space and then anything but a lowercase
    # letter: "etc. Electronic Goods ..." ends it, "Fabs./made-ups" and "excl. hand made carpet" do not.
    drv = re.search(rf"(?i:major drivers of merchandise exports? growth in {month},? {y} includes?) "
                    rf"(.+?)\.(?=\s+[^a-z\s]|\s*$)", txt)
    drivers = drv.group(1).strip() if drv else ""
    if len(drivers) > 400 or re.search(r"(?i)billion|%", drivers):
        log(f"WARNING: the 'major drivers' sentence did not read cleanly, leaving it out: {drivers[:100]!r}")
        drivers = ""
    return {"headline_bn": float(head.group(1)),
            "headline_prev_bn": float(head.group(2)) if head.group(2) else None, "drivers": drivers}


def find_pib_release(key):
    """The month's trade release on PIB: dict(release, quick_pdf, drivers, headline_bn, headline_prev_bn)."""
    y, m = map(int, key.split("-"))
    ry, rm = release_month(key)
    month = MONTHS[m - 1]
    page = http("GET", PIB_LIST)
    form = parse(page.text).fields
    if "__VIEWSTATE" not in form:
        log(f"PIB: release list not available from here ({describe(page)})")
        return None
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
        info = read_release(text_of(r.text), key)
        if not info:
            continue
        pdf = None
        for href, label in parse(r.text).links:
            if "quick estimate" in label.lower() and href.lower().split("?")[0].endswith(".pdf"):
                pdf = urljoin(url, href)
                break
        log(f"PIB release found: PRID {prid}: {title[:110]}")
        info.update(release=url, quick_pdf=pdf)
        return info
    return None


def find_commerce_release(key):
    """The same release from the Department of Commerce list (as PDFs). Same dict as find_pib_release; when
    only the quick-estimates PDF is listed, the dict has no headline."""
    y, m = map(int, key.split("-"))
    month = MONTHS[m - 1]
    r = http("GET", COMMERCE_API)
    try:
        items = r.json()
    except ValueError:
        items = None
    if not isinstance(items, list):
        log(f"Commerce: release list not available from here ({describe(r)})")
        return None
    this_month = re.compile(rf"\b{month}\W{{0,3}}{y}\b", re.I)
    release = quick = None
    for d in items:
        if not isinstance(d, dict):
            continue
        title = html.unescape(str(d.get("title") or ""))
        path = unquote(str(d.get("field_upload_file") or "")).strip()
        if not path.lower().endswith(".pdf"):
            continue
        url = path.replace(" ", "%20") if re.match(r"https?://", path) else urljoin(COMMERCE, quote(path))
        if quick is None and re.search(r"(?i)quick estimates? for selected major commodities", title) \
                and this_month.search(title):
            quick = url
        elif release is None and re.search(r"(?i)monthly press release on india.{0,3}s foreign trade", title) \
                and (this_month.search(path) or this_month.search(title)):
            release = url
    log(f"Commerce: {len(items)} releases listed; {month} {y}: trade release "
        f"{'found' if release else 'not listed'}, quick estimates {'found' if quick else 'not listed'}")
    info = None
    if release:
        raw = http("GET", release).content
        info = read_release(" ".join(um.pdf_pages(raw)), key) if raw[:4] == b"%PDF" else None
        if not info:
            log("Commerce: the trade release PDF does not have the expected headline, so it was not used")
    if info:
        info.update(release=release, quick_pdf=quick)
    elif quick:
        info = {"release": None, "quick_pdf": quick, "drivers": "", "headline_bn": None, "headline_prev_bn": None}
    return info


def lookup(key):
    """The month's trade release from the first source that has it (or a figures-only result)."""
    partial = None
    for name, find in (("PIB", find_pib_release), ("Commerce", find_commerce_release)):
        try:
            info = find(key)
        except Exception as e:  # noqa: BLE001  one source being down must not stop the others
            log(f"{name}: lookup failed ({e})")
            continue
        if info and info.get("headline_bn") is not None:
            return info
        partial = partial or info
    return partial


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


def read_pdf(url, key):
    raw = http("GET", url).content
    if raw[:4] != b"%PDF":
        um.fail(f"{url} did not return a PDF")
    return um.parse_quick(raw, key)


def records_match(a, b):
    return all(abs(a[n][k] - b[n][k]) < 0.0051 for n in um.ROWS for k in ("value", "previous", "growth"))


def process(key, data):
    """Add one month. Returns 'added' or 'missing'."""
    info = lookup(key) or {}
    pdf = info.get("quick_pdf") or find_dgcis_pdf(key)
    if not pdf:
        return "missing"
    sectors = read_pdf(pdf, key)
    t = sectors["GRAND TOTAL"]
    if info.get("headline_bn") is not None:
        prev_bn = info["headline_prev_bn"]
        if abs(t["value"] / 1000 - info["headline_bn"]) > 0.006 or \
                (prev_bn is not None and abs(t["previous"] / 1000 - prev_bn) > 0.006):
            um.fail(f"PDF total {t['value']} / {t['previous']} does not match the press release headline "
                    f"US$ {info['headline_bn']} bn / {info['headline_prev_bn']} bn")
        log(f"Press release headline matches the PDF total (US$ {info['headline_bn']} bn).")
    else:
        log("NOTE: the press release could not be read from here, so the headline cross-check was skipped. "
            "Every check on the PDF itself still ran.")
    release = info.get("release") or COMMERCE_LIST
    if not um.official(pdf) or not um.official(release):
        um.fail(f"non-official link: {pdf} / {release}")
    drivers = um.clean_text(info.get("drivers", ""))
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
    log(f"  Ministry highlights: {drivers or '(not available here; add them with Run workflow if wanted)'}")
    return "added"


def verify(keys, data):
    """Re-read stored months from the PDFs they cite. Writes nothing; fails if any month differs."""
    bad = []
    for key in keys:
        rec = next(d for d in data if d["key"] == key)
        try:
            sectors = read_pdf(rec["source"], key)
        except (SystemExit, RuntimeError) as e:
            log(f"VERIFY {key}: could not read {rec['source']} ({'see above' if isinstance(e, SystemExit) else e})")
            bad.append(key)
            continue
        if records_match(rec["sectors"], sectors):
            log(f"VERIFY {key}: MATCHES the official PDF ({rec['source']})")
        else:
            log(f"VERIFY {key}: DIFFERS FROM the official PDF ({rec['source']})")
            for n in um.ROWS:
                log(f"  {n}: stored {rec['sectors'][n]} pdf {sectors[n]}")
            bad.append(key)
    if bad:
        um.fail(f"{len(bad)} of {len(keys)} months did not verify: {', '.join(bad)}")
    log(f"VERIFIED: {len(keys)} month(s) match their official PDFs.")


def annotate(key, drivers, release, data):
    """Add the Ministry's 'major drivers' list and/or the release link to a stored month."""
    rec = next((d for d in data if d["key"] == key), None)
    if rec is None:
        um.fail(f"to add highlights, give a month that is already in data.json (YYYY-MM), got {key!r}")
    if drivers:
        drivers = re.sub(r"(?is)^\s*major drivers of merchandise exports? growth in .*?\binclude[sd]?\s+", "", drivers)
        drivers = um.clean_text(drivers)
        if not 3 <= len(drivers) <= 400 or re.search(r"https?://|www\.|[<>]", drivers):
            um.fail("the drivers box should hold only the Ministry's list (plain text, under 400 characters)")
        rec["officialDrivers"] = drivers
    if release:
        if not um.official(release):
            um.fail(f"the release link must be an https link on {', '.join(um.OFFICIAL_HOSTS)}: {release!r}")
        rec["release"] = release
    um.check_series(data)
    log(f"{rec['month']}: Ministry highlights: {rec['officialDrivers'] or '(none)'}")
    log(f"{rec['month']}: release link: {rec['release']}")
    return rec["month"]


def save(data, msg):
    um.DATA_JSON.write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    out = os.environ.get("COMMIT_MSG_FILE")
    if out:
        Path(out).write_text(msg + "\n", encoding="utf-8")
    log("WRITTEN:", msg)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--verify", help="YYYY-MM of a stored month, or 'all', to re-check against the PDFs they cite")
    ap.add_argument("--annotate", help="YYYY-MM of a stored month to add the Ministry's highlights to")
    ap.add_argument("--drivers", help="with --annotate: the Ministry's 'major drivers' list for that month")
    ap.add_argument("--release", help="with --annotate: link to that month's release on pib.gov.in or commerce.gov.in")
    a = ap.parse_args()
    env = os.environ.get
    verify_arg = (a.verify or env("VERIFY_MONTH", "")).strip()
    note_month = (a.annotate or env("ANNOTATE_MONTH", "")).strip()
    note_drivers = (a.drivers or env("ANNOTATE_DRIVERS", "")).strip()
    note_release = (a.release or env("ANNOTATE_RELEASE", "")).strip()
    data = json.loads(um.DATA_JSON.read_text(encoding="utf-8"))
    um.check_series(data)
    keys = [d["key"] for d in data]
    if verify_arg:
        if verify_arg.lower() != "all" and verify_arg not in keys:
            um.fail(f"--verify needs a month that is already in data.json, or 'all', got {verify_arg!r}")
        verify(keys if verify_arg.lower() == "all" else [verify_arg], data)
        return
    if note_month or note_drivers or note_release:
        if not note_month or not (note_drivers or note_release):
            um.fail("to add highlights, give the month plus the drivers list and/or the release link")
        save(data, f"Add Ministry highlights for {annotate(note_month, note_drivers, note_release, data)}")
        return
    added = []
    while True:
        key = um.next_key(data[-1]["key"])
        if process(key, data) != "added":
            ry, rm = release_month(key)
            log(f"NO NEW RELEASE YET for {um.month_label(key)} (expected around 15 {MONTHS[rm - 1]} {ry}).")
            if not added and dt.date.today() >= dt.date(ry, rm, 25):
                log("The release is overdue or could not be found. Please check PIB and DGCI&S manually.")
                sys.exit(1)
            break
        added.append(um.month_label(key))
    if added:
        save(data, "Add " + ", ".join(added) + " quick estimates")


if __name__ == "__main__":
    main()
