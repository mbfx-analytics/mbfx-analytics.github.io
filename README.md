# mbfx-analytics.github.io

GitHub Pages site hosting MultiBank FX analytics reports. Live at
https://mbfx-analytics.github.io/

## Structure

One folder per report, each containing an `index.html`. The folder name becomes the URL path.

```
/index.html                            ->  /                        hub / index of reports
/landing-page-analysis/index.html      ->  /landing-page-analysis/
/google-ads-dashboard/index.html       ->  /google-ads-dashboard/
/sales-feedback-analysis/index.html    ->  /sales-feedback-analysis/
/assets/site.css                       ->  shared MBFX styling for hub + placeholders
/.nojekyll                             ->  serve files as-is, skip Jekyll
```

## Adding a new report

1. Create a folder named after the URL you want, lowercase and hyphenated.
2. Put the report in it as `index.html`.
3. Add a card for it in the root `index.html`.

Use root-absolute paths (`/assets/logo.png`) for shared assets, or paths relative to the
report's own folder. Report HTML should otherwise be self-contained.

## Moving a file in the GitHub web UI

Open the file, click the pencil (Edit) icon, then edit the filename box to include the new
folder path — e.g. change `index.html` to `landing-page-analysis/index.html` — and commit.
GitHub performs the move server-side, with no download or re-upload.
