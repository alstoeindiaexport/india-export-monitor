# India Export Monitor: data feed

This repository is the data feed behind the **India Export Monitor** on
[alstoeindiaexports.com](https://alstoeindiaexports.com/india-export-monitor/).
The dashboard tracks India's monthly merchandise exports for five sectors, plus total merchandise exports:

- Electronic Goods
- Engineering Goods
- Petroleum Products
- Marine Products
- Drugs & Pharmaceuticals

Coverage starts in April 2025. One month is added after each official release.

## Files

| File | What it is |
|---|---|
| `data.json` | The data. Each month is one record. The website widget reads this file on every visit. |
| `tools/update_month.py` | Reads and validates a quick-estimates PDF. Nothing is written unless every check passes. |
| `tools/auto_update.py` | Finds each new official release and adds the month, using `update_month.py` for every check. Also re-checks stored months and adds the Ministry's highlights. |
| `.github/workflows/monthly-update.yml` | Runs `auto_update.py` on GitHub every month and commits the result. |

## Record format

```json
{
  "month": "August 2026",
  "key": "2026-08",
  "source": "https://static.pib.gov.in/.../quick-estimates.pdf",
  "sectors": {
    "Electronic Goods": {"value": 5553.09, "previous": 2925.52, "growth": 89.82},
    "GRAND TOTAL": {"value": 43810.92, "previous": 34736.67, "growth": 26.12}
  },
  "officialDrivers": "Electronic Goods, Petroleum Products, ...",
  "release": "https://www.pib.gov.in/PressReleasePage.aspx?PRID=..."
}
```

- `value`: exports in the month, in US$ million.
- `previous`: the same month a year earlier, as printed in the same release (the revised base).
- `growth`: the published year-on-year change, in percent.
- `source`: the official quick-estimates PDF the figures were read from.
- `officialDrivers`: the Ministry's "major drivers of merchandise exports growth" list for that month (empty until added).
- `release`: the Ministry's monthly press release, or the Department of Commerce release list when the release
  itself could not be read.

## Sources

Only official sources are used:

1. The DGCI&S / Department of Commerce PDF titled **"Quick Estimates for Selected Major Commodities for <Month> <Year>"**,
   table "TRADE: EXPORT (Values in Million USD)", published on PIB, commerce.gov.in and dgciskol.gov.in.
2. The Ministry of Commerce & Industry monthly trade press release on pib.gov.in (also posted as a PDF on
   commerce.gov.in).

## Method

- **Release vintage.** Each month keeps the figures from its own provisional release. Earlier months are never
  overwritten with later revisions, so a 2026 growth rate does not always match the growth you would calculate
  from the 2025 chart points.
- **Checks before writing.** The updater confirms the PDF title, month and table; checks that each published
  growth rate matches the growth recomputed from the two values (to within 0.012 percentage points, which
  catches misread digits); checks the April-to-date columns the same way; checks that the five sectors add up
  to less than the total; checks the grand total against the press release headline when the release can be
  read; checks that months are added in order with no gaps; and accepts only https links on official
  government hosts. It warns when a year-ago base differs from the stored figure by more than 10%.

## Monthly update

The GitHub Actions workflow `.github/workflows/monthly-update.yml` runs every day from the 14th to the 28th at
10:00 IST. It looks for the new month's release in this order: the Ministry's release list on PIB, the
Department of Commerce release list, then the DGCI&S Quick Estimates page. It reads the quick-estimates PDF,
runs every check in `tools/update_month.py`, and commits `data.json` only if everything passes. Once the month
is in, the remaining runs that month do nothing.

PIB and commerce.gov.in currently refuse requests from GitHub's servers (HTTP 403), so on GitHub the figures
come from the DGCI&S PDF. Every check on the PDF still runs, but the press release headline cross-check is
skipped, the month is added without the Ministry's "major drivers" list, and its release link points to the
Department of Commerce release list. The dashboard then shows "See the monthly release for the Ministry's full
commentary" for that month. The highlights can be added afterwards (see below).

If a check fails, or the release still cannot be found on the 25th, the run fails and GitHub emails the
repository owner. Nothing is written in either case, so the website keeps showing the last good data.

GitHub pauses scheduled workflows in a public repository after 60 days without any commit. The monthly data
commits keep it active; if it is ever paused, open **Actions > Monthly export data update** and click
**Enable workflow**.

### Run it by hand

**Actions > Monthly export data update > Run workflow**, then:

- **Look for new months now:** leave every box empty.
- **Add the Ministry's highlights to a month:** open that month's release on PIB, copy the list that follows
  "Major drivers of merchandise exports growth in <month> include", and enter the month (for example
  `2026-09`), the list, and the PIB link. The list is cleaned (dashes removed) and the link must be on
  pib.gov.in or commerce.gov.in.
- **Re-check stored figures:** enter a month such as `2026-08`, or `all`, in the first box. Each month is read
  again from the PDF it cites and compared. Nothing is changed.

### From a computer

```bash
pip install -r requirements.txt
python tools/update_month.py --check
python tools/auto_update.py                       # add newly published months
python tools/auto_update.py --verify all          # re-check every month
python tools/auto_update.py --annotate 2026-09 --drivers "Electronic Goods, Rice and ..." --release "<PIB link>"
python tools/update_month.py --month 2026-09 --quick-pdf "<PDF URL>" --release "<release URL>" --dry-run
```

On a computer that can reach PIB or commerce.gov.in, `auto_update.py` also adds the Ministry's highlights and
runs the headline cross-check on its own.
