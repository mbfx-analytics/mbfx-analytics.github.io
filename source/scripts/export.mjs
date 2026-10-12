// Copies the built single-file dashboard to exports/: a dated standalone file and
// a ready-to-upload GitHub Pages folder (index.html + .nojekyll).
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";

const root = join(dirname(fileURLToPath(import.meta.url)), "..");
const data = JSON.parse(readFileSync(join(root, "src/data/report.json"), "utf8"));
const asOf = data.trend.dates[data.days - 1]; // last day with data, e.g. 2026-10-08
const out = join(root, "exports");
const pages = join(out, "github-pages");
mkdirSync(pages, { recursive: true });

const built = join(root, "dist/index.html");
const dated = join(out, `Marketing-Performance-Dashboard-${asOf}.html`);
copyFileSync(built, dated);
copyFileSync(built, join(pages, "index.html"));
writeFileSync(join(pages, ".nojekyll"), ""); // serve the file as-is, no Jekyll processing

const kb = (readFileSync(built).length / 1024).toFixed(0);
console.log(`Exported ${kb} KB\n  ${dated}\n  ${join(pages, "index.html")}`);
