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
| `tools/auto_update.py` | Finds each new official release and adds the month, using `update_month.py` for every check. |
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
- `officialDrivers`: the Ministry's "major drivers of merchandise exports growth" list for that month.

## Sources

Only official sources are used:

1. The DGCI&S / Department of Commerce PDF titled **"Quick Estimates for Selected Major Commodities for <Month> <Year>"**,
   table "TRADE: EXPORT (Values in Million USD)", published on PIB.
2. The Ministry of Commerce & Industry monthly trade press release on pib.gov.in.

## Method

- **Release vintage.** Each month keeps the figures from its own provisional release. Earlier months are never
  overwritten with later revisions, so a 2026 growth rate does not always match the growth you would calculate
  from the 2025 chart points.
- **Checks before writing.** The updater confirms the PDF title, month and table; checks that each published
  growth rate matches the growth recomputed from the two values (to within 0.012 percentage points, which
  catches misread digits); checks the April-to-date columns the same way; checks the grand total against the
  press release headline; checks that months are added in order with no gaps; and accepts only https links on
  official government hosts. It warns when a year-ago base differs from the stored figure by more than 10%.

## Monthly update

The GitHub Actions workflow `.github/workflows/monthly-update.yml` runs every day from the 14th to the 28th at
10:00 IST. It looks up the Ministry of Commerce & Industry's monthly trade release on PIB, downloads the DGCI&S
quick-estimates PDF linked from it (or, failing that, from the DGCI&S Quick Estimates page), reads the figures,
runs every check in `tools/update_month.py`, compares the total with the press release headline, and commits
`data.json` only if everything passes. Once the month is in, the remaining runs that month do nothing.

If a check fails, or the release still cannot be found on the 25th, the run fails and GitHub emails the
repository owner. Nothing is written in either case, so the website keeps showing the last good data.

To run it by hand: **Actions > Monthly export data update > Run workflow**. Enter a month such as `2026-08` in
the box to re-check that month against its official PDF without changing anything.

To add a month by hand:

```bash
python tools/update_month.py --check
python tools/update_month.py --month 2026-09 --quick-pdf "<PDF URL>" --release "<release URL>" --dry-run
python tools/update_month.py --month 2026-09 --quick-pdf "<PDF URL>" --release "<release URL>"
```

(The PDF mode needs `pip install pdfplumber requests`.)
