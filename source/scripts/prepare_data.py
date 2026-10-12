"""Build the dashboard dataset (src/data/report.json) from the raw pipeline output.

Inputs (newest of each is used when no arguments are given)
  data/raw/D-YYYY-MM-DD.json                         the `D` object from the Power BI pipeline
  data/raw/Lead_Distribution_Marketing_Budget_*.xlsx  monthly budget + desk share matrix

Steps (each one checked; the script exits non-zero on any failed check, so an
automated refresh never publishes numbers that don't reconcile):
  0. Validates the input shape (keys, array lengths, markets, weekly labels).
  1. Reads the budget workbook: per-country plan (budget, CPL, leads), desk shares, team plans.
  2. Removes Demo Competition from every total, asset table and narrative
     (skipped automatically per market if the pipeline already excluded it).
  3. Recomputes derived metrics (CPC, CPL, CPA, CPFA, conversion %, NMI, ROI).
  4. Rounds allocated (fractional) funded counts in asset rows to whole accounts.
  5. Rebuilds grand totals, plans, desk leads (share matrix × cleaned leads), plan actions.
  6. Calibrates the daily trend per week to the reconciled weekly totals.

Run:  python3 -I scripts/prepare_data.py                      # newest inputs in data/raw/
      python3 -I scripts/prepare_data.py <D.json> <budget.xlsx>
"""

import json
import re
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import openpyxl

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw"
OUT = ROOT / "src" / "data" / "report.json"

CHANNELS = ("PPC", "Social")
SUM_KEYS = ("spend", "clicks", "leads", "accounts", "funded", "tdep", "twdr")
DEMO_ASSET = "Demo Competition"
DEMO_QUALITY_KEYS = ("Demo-Competition", "Demo")
DEMO_TEXT = re.compile(r"demo[\s-]*(competition|winner|comp\b)", re.I)
COUNTRY_CODE = {"Saudi Arabia": "KSA"}  # workbook name -> dataset code (others identical)
MONTHS = {m: i + 1 for i, m in enumerate(["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"])}

log_lines: list[str] = []
errors: list[str] = []
warnings: list[str] = []


def log(msg: str) -> None:
    log_lines.append(msg)
    print(msg)


def fail(msg: str) -> None:
    errors.append(msg)
    log(f"  ERROR {msg}")


def warn(msg: str) -> None:
    warnings.append(msg)
    log(f"  WARN  {msg}")


def die_if_errors(stage: str) -> None:
    if errors:
        print(f"\nFAILED at {stage}: {len(errors)} error(s). Nothing was written.", file=sys.stderr)
        for e in errors:
            print(f"  - {e}", file=sys.stderr)
        sys.exit(1)


def div(a, b):
    return a / b if b else None


def finalize(m: dict) -> dict:
    """Recompute every derived metric from the summable base metrics."""
    out = {k: (m.get(k) or 0) for k in SUM_KEYS}
    out["cpc"] = div(out["spend"], out["clicks"])
    out["cpl"] = div(out["spend"], out["leads"])
    out["cpa"] = div(out["spend"], out["accounts"])
    out["cpfa"] = div(out["spend"], out["funded"])
    out["l2a"] = div(100 * out["accounts"], out["leads"])
    out["a2f"] = div(100 * out["funded"], out["accounts"])
    out["nmi"] = out["tdep"] + out["twdr"]
    out["roi"] = div(out["nmi"], out["spend"])
    return out


def add(*ms: dict) -> dict:
    return {k: sum((m.get(k) or 0) for m in ms) for k in SUM_KEYS}


def num(v) -> float:
    return float(v) if isinstance(v, (int, float)) else 0.0


def close(a: float, b: float, rel: float = 0.005, abs_: float = 0.5) -> bool:
    return abs(a - b) <= max(abs_, rel * abs(b))


# ─── Inputs ───────────────────────────────────────────────────────────────────

def pick_inputs() -> tuple[Path, Path]:
    if len(sys.argv) == 3:
        return Path(sys.argv[1]), Path(sys.argv[2])
    ds = sorted(RAW_DIR.glob("D-*.json"))  # ISO date in the name → lexical = chronological
    bs = sorted(RAW_DIR.glob("Lead_Distribution_Marketing_Budget_*.xlsx"), key=lambda p: p.stat().st_mtime)
    if not ds or not bs:
        print(f"Missing inputs in {RAW_DIR}: need D-YYYY-MM-DD.json and Lead_Distribution_Marketing_Budget_*.xlsx", file=sys.stderr)
        sys.exit(1)
    return ds[-1], bs[-1]


RAW_D, BUDGET_XLSX = pick_inputs()
log(f"Inputs: {RAW_D.name} + {BUDGET_XLSX.name}")


# ─── 0. Validate input shape ──────────────────────────────────────────────────

D = json.loads(RAW_D.read_text())

for k in ("days", "dim", "period", "asOf", "periodStart", "periodEnd", "show", "rows", "grandAssets",
          "grandAssetsQuality", "assetTypes", "desks", "execSummary", "trend", "dayEvents"):
    if k not in D:
        fail(f"top-level key missing: {k}")
die_if_errors("input validation")

dates = D["trend"].get("dates", [])
if len(dates) != D["dim"]:
    fail(f"trend.dates has {len(dates)} days but dim = {D['dim']}")
if not (1 <= D["days"] <= len(dates)):
    fail(f"days = {D['days']} must be between 1 and {len(dates)}")
if dates and dates[0] != D["periodStart"]:
    fail(f"trend.dates[0] {dates[0]} != periodStart {D['periodStart']}")
for ch in ("PPC", "Social", "Overall"):
    for k in ("spend", "leads", "l2l"):
        arr = D["trend"].get(ch, {}).get(k)
        if arr is None or len(arr) != len(dates):
            fail(f"trend.{ch}.{k} missing or not {len(dates)} long")
        elif any(v is None for v in arr[: D["days"]]):
            fail(f"trend.{ch}.{k} has nulls inside the first {D['days']} (actual) days")
row_codes = [r["c"] for r in D["rows"]]
if sorted(row_codes) != sorted(D["show"]):
    fail(f"rows {sorted(row_codes)} don't match show list {sorted(D['show'])}")
for r in D["rows"]:
    for k in ("c", "name", "flag", "status", "quality", "ch", "ppcSplit", "assets", "weekly"):
        if k not in r:
            fail(f"row {r.get('c')} missing key {k}")
    for ch in ("PPC", "Social", "Overall"):
        if "a" not in r.get("ch", {}).get(ch, {}):
            fail(f"row {r.get('c')} missing ch.{ch}.a")
labels = [w["label"] for w in D["rows"][0]["weekly"]] if D["rows"] else []
for r in D["rows"]:
    if [w["label"] for w in r["weekly"]] != labels:
        fail(f"row {r['c']} weekly labels {[w['label'] for w in r['weekly']]} differ from {labels}")
for iso in D["dayEvents"]:
    if not (D["periodStart"] <= iso <= D["periodEnd"]):
        warn(f"dayEvents date {iso} is outside the period {D['periodStart']}–{D['periodEnd']}")
die_if_errors("input validation")

# Weekly buckets from their labels ("10–16 Sep", "29 Sep–5 Oct", "1–8 Oct").
period_start = date.fromisoformat(D["periodStart"])
last_actual = date.fromisoformat(dates[D["days"] - 1])


def parse_week(label: str, after: date) -> tuple[date, date]:
    m = re.fullmatch(r"\s*(\d{1,2})(?:\s*([A-Za-z]{3}))?\s*[–—-]\s*(\d{1,2})\s*([A-Za-z]{3})\s*", label)
    if not m:
        raise ValueError(f"unrecognised weekly label {label!r} (expected like '10–16 Sep' or '29 Sep–5 Oct')")
    d1, m1, d2, m2 = int(m[1]), m[2], int(m[3]), m[4]
    mon2 = MONTHS[m2[:3].title()]
    mon1 = MONTHS[m1[:3].title()] if m1 else mon2
    y = after.year
    start = date(y, mon1, d1)
    if start < after - timedelta(days=200):  # crossed a year boundary
        start = date(y + 1, mon1, d1)
    end = date(start.year + (1 if mon2 < mon1 else 0), mon2, d2)
    return start, end


week_ranges: list[tuple[date, date]] = []
cursor = period_start
for lab in labels:
    try:
        s, e = parse_week(lab, cursor)
    except ValueError as ex:
        fail(str(ex))
        break
    if s != cursor:
        fail(f"weekly bucket {lab!r} starts {s} but the previous bucket ended {cursor - timedelta(days=1)} (gap or overlap)")
    week_ranges.append((s, e))
    cursor = e + timedelta(days=1)
if week_ranges and week_ranges[-1][1] != last_actual:
    fail(f"weekly buckets end {week_ranges[-1][1]} but the last day with data is {last_actual} (days = {D['days']})")
die_if_errors("weekly labels")
for r in D["rows"]:
    for w, (s, e) in zip(r["weekly"], week_ranges):
        w["start"], w["end"], w["days"] = s.isoformat(), e.isoformat(), (e - s).days + 1
log(f"Period {D['periodStart']} → {D['periodEnd']}, data through {last_actual} (day {D['days']}/{D['dim']}), "
    f"{len(labels)} weekly buckets: {', '.join(labels)}")


def week_index(iso: str) -> int | None:
    d = date.fromisoformat(iso)
    for i, (s, e) in enumerate(week_ranges):
        if s <= d <= e:
            return i
    return None


# ─── 1. Budget workbook ───────────────────────────────────────────────────────

def read_budget(path: Path):
    ws = openpyxl.load_workbook(path, data_only=True).worksheets[0]
    rows = [r[1:13] for r in ws.iter_rows(values_only=True)]
    plan: dict[str, dict] = {}
    shares: dict[str, list[tuple[str, float]]] = {}
    team_plan: dict[str, dict] = {}

    # Section 1 — per sales team monthly plan (budget, leads).
    sec1 = next(i for i, r in enumerate(rows) if r[0] == "Sales team")
    for r in rows[sec1 + 1:]:
        if not r[0] or str(r[0]).startswith("TOTAL"):
            break
        team_plan[r[0]] = {
            "markets": r[1],
            "ppc": {"spend": num(r[2]), "leads": num(r[4])},
            "soc": {"spend": num(r[5]), "leads": num(r[7])},
            "staff": r[10] if isinstance(r[10], (int, float)) else None,
        }

    # Section 3 — country totals followed by their sales-team shares.
    sec3 = next(i for i, r in enumerate(rows) if r[0] == "Region  /  Country  /  Sales team")
    country = None
    for r in rows[sec3 + 1:]:
        label, share = r[0], r[1]
        if not label or str(label).startswith("TOTAL"):
            continue
        if share == "country total":
            country = COUNTRY_CODE.get(label, label)
            plan[country] = {
                "PPC": {"spend": num(r[2]), "cpl": r[3] if isinstance(r[3], (int, float)) else None, "leads": num(r[4])},
                "Social": {"spend": num(r[5]), "cpl": r[6] if isinstance(r[6], (int, float)) else None, "leads": num(r[7])},
            }
            shares[country] = []
        elif isinstance(share, (int, float)) and country:
            shares[country].append((label, float(share)))
        else:
            country = None  # region header row
    return plan, shares, team_plan


try:
    plan, shares, team_plan = read_budget(BUDGET_XLSX)
except StopIteration:
    fail(f"{BUDGET_XLSX.name}: couldn't find the 'Sales team' or 'Region / Country / Sales team' sections")
    die_if_errors("budget workbook")
log(f"Budget workbook: {len(plan)} countries, {len(team_plan)} sales teams")

for c in D["show"]:
    if c not in plan:
        fail(f"market {c} has no 'country total' row in the budget workbook")
    elif not shares.get(c):
        fail(f"market {c} has no sales-desk shares in the budget workbook")
for c, ss in shares.items():
    if ss and abs(sum(s for _, s in ss) - 1) > 1e-6:
        fail(f"desk shares for {c} sum to {sum(s for _, s in ss):.3f}, not 1")
    for team, _ in ss:
        if team not in team_plan:
            fail(f"desk {team!r} (in {c}) is missing from the 'Sales team' section")
die_if_errors("budget workbook")

# Workbook vs pipeline plan: the workbook wins, but a difference is worth knowing.
for r in D["rows"]:
    for ch in CHANNELS:
        e, b = r["ch"][ch].get("e") or {}, plan[r["c"]][ch]
        if abs((e.get("spend") or 0) - b["spend"]) > 1 or abs((e.get("leads") or 0) - b["leads"]) > 0.5:
            warn(f"plan differs for {r['c']} {ch}: pipeline {e} vs workbook {b} (workbook used)")


def plan_action(r: dict, overall: dict, e: dict | None) -> str:
    pace = div(overall["spend"], (e or {}).get("spend", 0) * D["days"] / D["dim"]) if e else None
    q = r["quality"]
    bad_frac = div(q["bad"] + q["cna"], q["total"]) if q["total"] >= 5 else None
    if pace is not None and pace >= 1.30:
        return "Pull spend"
    if bad_frac is not None and bad_frac >= 0.65:
        return "Optimize"
    if pace is not None and pace <= 0.70:
        return "Scale"
    return "Hold"


# ─── 2–4. Clean rows ──────────────────────────────────────────────────────────

demo_total = {k: 0.0 for k in SUM_KEYS}
already_clean = []
rounded_funded = []
for r in D["rows"]:
    demo = (r.get("assets", {}).get("Social") or {}).pop(DEMO_ASSET, None)
    social = r["ch"]["Social"]["a"]
    if demo and (demo.get("spend") or demo.get("leads")):
        # Guard against double subtraction: weekly figures always exclude Demo.
        # If the headline already equals the weekly sum, the pipeline removed it.
        # Decide on leads when the Demo row has leads (some have leads but $0 spend).
        k = "leads" if demo.get("leads") else "spend"
        weekly = sum(w["ch"]["Social"]["a"].get(k) or 0 for w in r["weekly"])
        total = social.get(k) or 0
        if close(weekly, total, rel=0, abs_=0.01) and not close(weekly, total - demo[k], rel=0, abs_=0.01):
            already_clean.append(r["c"])
        else:
            for k in SUM_KEYS:
                social[k] = (social.get(k) or 0) - (demo.get(k) or 0)
                demo_total[k] += demo.get(k) or 0
    r["ch"]["PPC"]["a"] = finalize(r["ch"]["PPC"]["a"])
    r["ch"]["Social"]["a"] = finalize(social)
    r["ch"]["Overall"]["a"] = finalize(add(r["ch"]["PPC"]["a"], r["ch"]["Social"]["a"]))

    # Plans straight from the workbook. A channel with no budget has no plan.
    for ch in CHANNELS:
        b = plan[r["c"]][ch]
        r["ch"][ch]["e"] = {"spend": b["spend"], "leads": b["leads"], "cpl": b["cpl"]} if b["spend"] > 0 else None
    es = [r["ch"][ch]["e"] for ch in CHANNELS if r["ch"][ch]["e"]]
    r["ch"]["Overall"]["e"] = (
        {"spend": sum(e["spend"] for e in es), "leads": sum(e["leads"] for e in es),
         "cpl": div(sum(e["spend"] for e in es), sum(e["leads"] for e in es))}
        if es else None
    )

    for ch in CHANNELS:
        for name, a in (r.get("assets", {}).get(ch) or {}).items():
            if a.get("funded") and a["funded"] % 1:
                rounded_funded.append((r["c"], ch, name, a["funded"]))
                a["funded"] = float(int(a["funded"] + 0.5))  # nearest whole account
            r["assets"][ch][name] = finalize(a)

    for w in r["weekly"]:
        for ch in CHANNELS:
            w["ch"][ch]["a"] = finalize(w["ch"][ch]["a"])
        w["ch"]["Overall"]["a"] = finalize(add(w["ch"]["PPC"]["a"], w["ch"]["Social"]["a"]))

    r["planAction"] = plan_action(r, r["ch"]["Overall"]["a"], r["ch"]["Overall"]["e"])

log(
    "Demo Competition removed: spend ${:,.2f}, clicks {:,.0f}, leads {:,.0f}, accounts {:,.0f}, funded {:,.0f}".format(
        demo_total["spend"], demo_total["clicks"], demo_total["leads"], demo_total["accounts"], demo_total["funded"]
    )
)
if already_clean:
    log(f"Demo Competition already excluded by the pipeline for: {', '.join(already_clean)} (not subtracted again)")
log(f"Rounded {len(rounded_funded)} fractional funded counts in asset rows to the nearest whole account")

# Weekly totals must tie to market totals exactly.
for r in D["rows"]:
    for k in ("spend", "leads", "accounts", "clicks"):
        w = sum(x["ch"]["Overall"]["a"][k] for x in r["weekly"])
        t = r["ch"]["Overall"]["a"][k]
        if not close(w, t):
            fail(f"weekly {k} for {r['c']} sums to {w:,.2f} but the market total is {t:,.2f}")
die_if_errors("weekly reconciliation")
log("Weekly vs market totals: all markets tie")

# ─── 5. Grand totals, assets, desks ───────────────────────────────────────────

rows = D["rows"]
D["grand"] = {ch: finalize(add(*(r["ch"][ch]["a"] for r in rows))) for ch in CHANNELS}
D["grand"]["Overall"] = finalize(add(D["grand"]["PPC"], D["grand"]["Social"]))
D["grandE"] = {}
for ch in CHANNELS:
    s = sum(plan[r["c"]][ch]["spend"] for r in rows)
    l = sum(plan[r["c"]][ch]["leads"] for r in rows)
    D["grandE"][ch] = {"spend": s, "leads": l, "cpl": div(s, l)}
s, l = D["grandE"]["PPC"]["spend"] + D["grandE"]["Social"]["spend"], D["grandE"]["PPC"]["leads"] + D["grandE"]["Social"]["leads"]
D["grandE"]["Overall"] = {"spend": s, "leads": l, "cpl": div(s, l)}
log(f"Plan: ${D['grandE']['Overall']['spend']:,.0f} (PPC ${D['grandE']['PPC']['spend']:,.0f} · Social ${D['grandE']['Social']['spend']:,.0f}), "
    f"{D['grandE']['Overall']['leads']:,.0f} leads/month")

for ch in CHANNELS:
    D["grandAssets"][ch].pop(DEMO_ASSET, None)
    merged: dict[str, dict] = {}
    for r in rows:
        for name, a in (r.get("assets", {}).get(ch) or {}).items():
            merged[name] = add(merged.get(name, {}), a)
    D["grandAssets"][ch] = {k: finalize(v) for k, v in merged.items()}
for k in DEMO_QUALITY_KEYS:
    D["grandAssetsQuality"]["Social"].pop(k, None)
# Align quality-key spelling with asset names so lookups don't silently miss.
q_social = D["grandAssetsQuality"]["Social"]
if "USP(s)" in q_social:
    q_social["USPs"] = q_social.pop("USP(s)")
D["assetTypes"] = [a for a in D["assetTypes"] if a != DEMO_ASSET]


# Desk leads: each desk's share of each country × that country's cleaned leads.
def desk_leads(rows_, ch_key):
    out: dict[str, float] = {}
    for r in rows_:
        for team, sh in shares.get(r["c"], []):
            out[team] = out.get(team, 0) + sh * (r["ch"][ch_key]["a"]["leads"] or 0)
    return out


ppc_d, soc_d = desk_leads(rows, "PPC"), desk_leads(rows, "Social")
desks = []
for team, tp in team_plan.items():
    a_ppc, a_soc = ppc_d.get(team, 0), soc_d.get(team, 0)
    desks.append({
        "team": team,
        "markets": tp["markets"],
        "staff": tp["staff"],
        "ppc": {"a": round(a_ppc, 1), "e": round(tp["ppc"]["leads"], 1)},
        "soc": {"a": round(a_soc, 1), "e": round(tp["soc"]["leads"], 1)},
        "tot": {"a": round(a_ppc + a_soc, 1), "e": round(tp["ppc"]["leads"] + tp["soc"]["leads"], 1)},
    })
D["desks"] = desks
D["deskTot"] = {
    k: {"a": round(sum(d[k]["a"] for d in desks), 1), "e": round(sum(d[k]["e"] for d in desks), 1)} for k in ("ppc", "soc", "tot")
}
D["deskTot"]["staff"] = sum(d["staff"] or 0 for d in desks)
D["deskShares"] = {c: [[t_, sh] for t_, sh in ss] for c, ss in shares.items()}
if not close(D["deskTot"]["tot"]["a"], D["grand"]["Overall"]["leads"], abs_=1):
    fail(f"desk leads {D['deskTot']['tot']['a']:,.1f} != total leads {D['grand']['Overall']['leads']:,.0f}")
log(f"Desks: {len(desks)} teams, {D['deskTot']['tot']['a']:,.0f} leads (= total leads)")

# ─── 6. Daily trend, calibrated to the reconciled weekly totals ──────────────
# The daily feed covers a wider scope than the 21-market/no-Demo totals. Each
# day is scaled by its week's factor (weekly total ÷ daily sum), which keeps the
# daily shape and makes the series tie to the totals. If the feed is re-pulled
# with the same filters, every factor is ~1.0 and this step changes nothing.

t = D["trend"]
weeks = labels
raw_trend = {ch: {k: list(t[ch][k]) for k in ("spend", "leads")} for ch in CHANNELS}
calib = {ch: {} for ch in CHANNELS}


def scale_by_week(series, weekly_total):
    factors = []
    for wi in range(len(weeks)):
        day_sum = sum(v for i, v in enumerate(series) if v is not None and week_index(dates[i]) == wi)
        week_sum = weekly_total(wi)
        if day_sum == 0 and week_sum:
            fail(f"daily feed has no values in week {weeks[wi]} but the weekly total is {week_sum:,.2f}")
        factors.append(div(week_sum, day_sum) or 0)
    scaled = [
        None if v is None or week_index(dates[i]) is None else v * factors[week_index(dates[i])]
        for i, v in enumerate(series)
    ]
    return scaled, factors


for ch in CHANNELS:
    for k in ("spend", "leads"):
        scaled, f = scale_by_week(raw_trend[ch][k], lambda wi, ch=ch, k=k: sum(r["weekly"][wi]["ch"][ch]["a"][k] for r in rows))
        t[ch][k] = [None if v is None else round(v, 2) for v in scaled]
        calib[ch][k] = [round(x, 4) for x in f]
for k in ("spend", "leads"):
    t["Overall"][k] = [None if a is None or b is None else round(a + b, 2) for a, b in zip(t["PPC"][k], t["Social"][k])]

# Lead → live-account (KYC) %: rebuild daily accounts from the raw ratio, scale
# per week to the cleaned weekly accounts, divide by the calibrated leads.
for ch in CHANNELS:
    raw_acc = [None if (p is None or l is None) else p * l / 100 for p, l in zip(t[ch]["l2l"], raw_trend[ch]["leads"])]
    scaled, f = scale_by_week(raw_acc, lambda wi, ch=ch: sum(r["weekly"][wi]["ch"][ch]["a"]["accounts"] for r in rows))
    t[ch]["accounts"] = scaled
    calib[ch]["accounts"] = [round(x, 4) for x in f]
t["Overall"]["accounts"] = [None if a is None or b is None else a + b for a, b in zip(t["PPC"]["accounts"], t["Social"]["accounts"])]
for ch in ("PPC", "Social", "Overall"):
    t[ch]["l2l"] = [None if (a is None or not l) else round(100 * a / l, 2) for a, l in zip(t[ch]["accounts"], t[ch]["leads"])]
    t[ch]["accounts"] = [None if a is None else round(a, 2) for a in t[ch]["accounts"]]
    t[ch]["cpl"] = [None if (s is None or not l) else round(s / l, 2) for s, l in zip(t[ch]["spend"], t[ch]["leads"])]
    t[ch]["planSpend"] = [D["grandE"][ch]["spend"] / D["dim"]] * len(dates)
    t[ch]["planLeads"] = [D["grandE"][ch]["leads"] / D["dim"]] * len(dates)
    t[ch]["planCpl"] = [D["grandE"][ch]["cpl"]] * len(dates)
    for k in ("remainSpend", "remainLeads"):
        t[ch].pop(k, None)
t["raw"] = raw_trend
t["calibration"] = {"weeks": weeks, "factors": calib}

for ch in ("PPC", "Social", "Overall"):
    for k in ("spend", "leads", "accounts"):
        s = sum(v for v in t[ch][k] if v is not None)
        g = D["grand"][ch][k]
        if not close(s, g, rel=0, abs_=1):
            fail(f"daily {ch} {k} sums to {s:,.2f} but the total is {g:,.2f}")
        else:
            log(f"Daily {ch} {k}: {s:,.2f} = total {g:,.2f}")
odd = [(ch, k, i, x) for ch in CHANNELS for k, fs in calib[ch].items() for i, x in enumerate(fs) if x and not (0.5 <= x <= 1.5)]
for ch, k, i, x in odd:
    warn(f"daily {ch} {k} scaled by {x:.2f} in week {weeks[i]}: the daily feed and weekly totals differ a lot")
log(f"Calibration factors (weekly ÷ raw daily; 1.0 = feed already matches): {json.dumps(calib)}")
die_if_errors("daily reconciliation")

# ─── Narrative: strip Demo Competition mentions ───────────────────────────────
# Rules: a clause (split on ";") about Demo Competition is dropped; inside a
# clause, text from the "and" that introduces the Demo part is cut. If the
# entry's leading clause is itself about Demo Competition, the whole entry goes
# (what follows usually depends on it).


def trim_clause(clause: str) -> str | None:
    if not DEMO_TEXT.search(clause):
        return clause
    head = re.split(r"\s+and\s+", clause, maxsplit=1)[0]
    return head if head != clause and not DEMO_TEXT.search(head) else None


def strip_demo(text: str) -> str | None:
    if not DEMO_TEXT.search(text):
        return text
    prefix = "Paid social: " if text.lower().startswith("paid social:") else ""
    body = (text[len(prefix):] if prefix else text).rstrip(".")
    clauses = [c.strip() for c in re.split(r";\s*", body) if c.strip()]
    first = trim_clause(clauses[0])
    if first is None:
        return None
    kept = [first] + [c for c in (trim_clause(c) for c in clauses[1:]) if c]
    out = prefix + "; ".join(kept)
    return out + "."


removed = 0
for iso in list(D["dayEvents"]):
    kept = []
    for e in D["dayEvents"][iso]:
        new = strip_demo(e["text"])
        removed += new != e["text"]
        if new:
            kept.append({**e, "text": new})
    if kept:
        D["dayEvents"][iso] = kept
    else:
        del D["dayEvents"][iso]
for k, v in D["execSummary"].items():
    if isinstance(v, list):
        cleaned = [strip_demo(x) for x in v]
        removed += sum(1 for a, b in zip(v, cleaned) if a != b)
        D["execSummary"][k] = [x for x in cleaned if x]
log(f"Narrative: {removed} Demo Competition mention(s) removed")

# ─── Output ───────────────────────────────────────────────────────────────────

D["meta"] = {
    "generatedAt": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "sources": [RAW_D.name, BUDGET_XLSX.name],
    "excludes": ["Demo Competition"],
    "notes": [
        "Demo Competition removed from all totals, assets, desks and narrative.",
        "Daily trend scaled per week to the reconciled weekly totals; re-pull the daily feed with the 21-market, no-Demo filter to remove this step.",
        "Asset-level funded counts are allocations, rounded to the nearest whole account.",
        "Lead-quality mix is recorded per market only and still includes Demo Competition leads.",
    ],
    "warnings": warnings,
    "log": log_lines,
}
OUT.parent.mkdir(parents=True, exist_ok=True)
OUT.write_text(json.dumps(D, separators=(",", ":"), ensure_ascii=False))
print(f"Wrote {OUT} ({OUT.stat().st_size / 1024:.0f} KB) with {len(warnings)} warning(s)")
