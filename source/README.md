# MBFX Marketing Performance Dashboard

A single self-contained HTML dashboard (PPC + Social across 21 markets) built with
React, Tailwind and shadcn components from evilcharts and bklit. The build inlines all
data, code, fonts and icons, so the file works offline and from any static host.

## Refresh the data

Daily automation brief (give this to the Claude session that pulls Power BI data):
[`docs/DAILY_REFRESH_HANDOFF.md`](docs/DAILY_REFRESH_HANDOFF.md). One command does everything:
`scripts/daily_refresh.sh` (set `PAGES_DIR` to also publish to GitHub Pages).

Manual steps:

1. Put the new pipeline dump and budget workbook in `data/raw/`.
2. Run (the newest `D-*.json` and budget workbook are picked automatically):

```bash
npm install            # first time only
npm run data           # clean + reconcile → src/data/report.json (prints a reconciliation log)
npm run export         # build + 649 headless checks + copy to exports/
```

`npm run data` removes Demo Competition, recomputes all derived metrics, rebuilds plans and
desk leads from the budget workbook, and scales the daily trend to the reconciled weekly
totals. Read its log: every total is printed next to what it should equal.

`npm run export` stops if any check fails.

## Outputs

| File | Use |
|---|---|
| `exports/Marketing-Performance-Dashboard-<date>.html` | Email / share drive / open from disk |
| `exports/github-pages/index.html` + `.nojekyll` | Upload as-is to a GitHub Pages repo |

## Hosting on GitHub Pages

1. Create a repository and upload the two files from `exports/github-pages/` to its root.
2. Settings → Pages → Source: *Deploy from a branch* → `main` / `(root)`.
3. The site is live at `https://<user>.github.io/<repo>/` within a minute or two.

**The dashboard contains all reporting data in plain text.** A GitHub Pages site is
public on the internet, even when the repository is private (unless the organisation has
GitHub Enterprise Cloud with private Pages). The page carries `noindex` so search engines
skip it, but anyone with the link can read every number. Use a private-Pages org, an
internal host, or share the file directly if the figures shouldn't be public.

## Develop

```bash
npm run dev            # local server with hot reload
npm run verify         # build + headless checks
```

Structure: `src/dashboard/` (sections), `src/components/` (shared UI, table kit, charts),
`src/lib/report.ts` (data access), `src/lib/recommendations.ts` (rules),
`scripts/prepare_data.py` (data pipeline), `tests/verify_dashboard.py` (checks).
