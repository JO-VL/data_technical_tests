"""
Build the client's WEEKLY KPI block for one week (Sunday to Saturday).

    python build_weekly_kpi.py --week 2026-W46

Writes:
    output/weekly_kpi_<week>.csv   same layout as client_sheet/weekly_kpi_template.csv
    output/checks_<week>.md        what was cleaned or looks wrong, for Laura to read

Only needs pandas. Re-running is safe: it rebuilds the block from the raw files,
so when a late retailer file arrives (e.g. Cultura+ on Wednesday) just run it again.
"""
import argparse
import re
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
TEMPLATE = HERE / "client_sheet" / "weekly_kpi_template.csv"
OUTPUT = HERE / "output"

REAL_REP = re.compile(r"^R\d+$")   # real field reps are R01..R10; anything else (QA_TEST) is not
MAX_RSAS_PER_VISIT = 20            # above this we assume a typo / test value


# ---------------------------------------------------------------- calendar
# Client rule: weeks run Sunday to Saturday, week 1 is the week that contains 1 January.
def week_start(label):
    """'2026-W46' -> Sunday 2026-11-08"""
    year, week = int(label[:4]), int(label[-2:])
    jan1 = date(year, 1, 1)
    sunday_of_week1 = jan1 - timedelta(days=(jan1.weekday() + 1) % 7)
    return sunday_of_week1 + timedelta(weeks=week - 1)


def week_label(day):
    """Any date -> client week label. A week belongs to the year of its Saturday."""
    sunday = day - timedelta(days=(day.weekday() + 1) % 7)
    year = (sunday + timedelta(days=6)).year
    return f"{year}-W{(sunday - week_start(f'{year}-W01')).days // 7 + 1:02d}"


def retailer_label(name):
    """'Galerías Prado' -> 'GALERIAS PRADO' (the label used in the client's sheet)."""
    no_accents = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return no_accents.upper()


# ---------------------------------------------------------------- visits
def clean_visits(start, end, checks):
    v = pd.read_csv(DATA / "field_visits.csv", parse_dates=["checkin_at", "checkout_at", "synced_at"])
    stores = pd.read_csv(DATA / "store_master.csv")

    # 1. Use the client's calendar (check-in date), NOT the app_week column (Monday-Sunday weeks).
    in_week = (v.checkin_at >= start) & (v.checkin_at < end)
    label = week_label(start.date())
    came_in = v[in_week & (v.app_week != label)]
    went_out = v[~in_week & (v.app_week == label)]
    if len(came_in) or len(went_out):
        checks.append(f"`app_week` uses Monday-Sunday weeks, the client uses Sunday-Saturday. Weeks assigned by check-in date "
                      f"instead: {len(came_in)} records added that the app labels another week, "
                      f"{len(went_out)} records labelled `{label}` moved to another week.")
    v = v[in_week]

    # 2. Test records
    test = v[~v.rep_code.astype(str).str.match(REAL_REP)]
    if len(test):
        checks.append(f"Removed {len(test)} test records (rep codes: {', '.join(sorted(test.rep_code.unique()))}).")
    v = v[v.rep_code.astype(str).str.match(REAL_REP)]

    # 3. Only completed visits count
    for status, n in v[v.status != "COMPLETED"].status.value_counts().items():
        ids = ", ".join(v[v.status == status].visit_id)
        checks.append(f"Excluded {n} {status} visit(s): {ids}." +
                      (" Ask the rep whether it was finished." if status == "IN_PROGRESS" else ""))
    v = v[v.status == "COMPLETED"]

    # 4. Same visit synced twice with a new visit_id: keep the first sync
    key = ["rep_code", "store_code", "checkin_at", "checkout_at"]
    v = v.sort_values("synced_at")
    dupes = v[v.duplicated(key, keep="first")]
    if len(dupes):
        checks.append(f"Removed {len(dupes)} duplicate records (same rep, store, check-in and check-out, synced twice): "
                      f"{', '.join(dupes.visit_id)}.")
    v = v.drop_duplicates(key, keep="first")

    # 5. Store must exist in our master
    unknown = v[~v.store_code.isin(stores.store_code)]
    if len(unknown):
        checks.append(f"Excluded {len(unknown)} visits to unknown stores: {', '.join(unknown.store_code.unique())}.")
    v = v.merge(stores[["store_code", "retailer"]], on="store_code")

    # 6. Implausible RSA numbers are flagged, not silently changed
    high = v[v.rsas_trained > MAX_RSAS_PER_VISIT]
    if len(high):
        checks.append(f"Check RSAs trained > {MAX_RSAS_PER_VISIT}: {', '.join(high.visit_id)}. Counted as 0.")
        v.loc[high.index, "rsas_trained"] = 0

    late = v[v.synced_at >= end + timedelta(days=1)]
    if len(late):
        checks.append(f"{len(late)} visits synced after Sunday (rep offline). Already included.")

    v["retailer"] = v.retailer.map(retailer_label)
    return v


# ---------------------------------------------------------------- sell-out
def clean_sellout(saturday, checks):
    s = pd.read_csv(DATA / "retailer_sellout.csv", parse_dates=["week_ending", "loaded_at"])
    stores = pd.read_csv(DATA / "store_master.csv")
    products = pd.read_csv(DATA / "product_master.csv")

    week = s[s.week_ending == pd.Timestamp(saturday)]

    # Map each row to our store (via the retailer's own code) and to a product line
    week = week.merge(stores[["retailer_store_code", "retailer", "store_name"]],
                      on="retailer_store_code", how="left", suffixes=("_file", ""))
    week = week.merge(products[["sku", "product_line"]], on="sku", how="left")
    bad = week[week.retailer.isna() | week.product_line.isna()]
    if len(bad):
        checks.append(f"Excluded {len(bad)} sell-out rows with unknown store code or SKU.")
    week = week.dropna(subset=["retailer", "product_line"])

    # If a retailer file was loaded twice, keep only the latest load
    key = ["retailer_store_code", "sku", "week_ending"]
    week = week.sort_values("loaded_at")
    if week.duplicated(key, keep="last").any():
        checks.append(f"Removed {week.duplicated(key, keep='last').sum()} sell-out rows loaded twice (kept latest).")
    week = week.drop_duplicates(key, keep="last")

    if (week.units < 0).any():
        checks.append("Negative sell-out units found (returns?). Kept as reported.")

    # Completeness: which retailers sent a file, and are all their stores in it?
    status = {}
    previous = s[s.week_ending == pd.Timestamp(saturday - timedelta(days=7))]
    for retailer, our_stores in stores.groupby("retailer"):
        label = retailer_label(retailer)
        got = week[week.retailer == retailer]
        if got.empty:
            status[label] = "missing"
            checks.append(f"**{retailer}: no sell-out file for this week yet.** Left blank; re-run when it arrives.")
            continue
        status[label] = "ok"
        missing = our_stores[~our_stores.retailer_store_code.isin(got.retailer_store_code)]
        if len(missing):
            status[label] = "partial"
            prev_units = previous[previous.retailer_store_code.isin(missing.retailer_store_code)].units.sum()
            checks.append(f"**{retailer}: {len(missing)} of {len(our_stores)} stores missing from the file** "
                          f"({', '.join(missing.store_name)}). They sold {prev_units} units the week before. "
                          f"Reported as received, so this line is understated. Ask the retailer for the missing stores.")

    week["retailer"] = week.retailer.map(retailer_label)
    return week, status


# ---------------------------------------------------------------- build the block
def build(week):
    start = week_start(week)
    saturday = start + timedelta(days=6)
    start_ts, end_ts = pd.Timestamp(start), pd.Timestamp(saturday + timedelta(days=1))
    checks = []

    visits = clean_visits(start_ts, end_ts, checks)
    sellout, sellout_status = clean_sellout(saturday, checks)

    # A "WEAR & XR" visit counts once in each line
    visits["line"] = visits.product_focus.str.split(" & ")
    by_line = visits.explode("line")

    # Same labels, same order and same columns as the client's template
    template = pd.read_csv(TEMPLATE)
    rows = []
    for label in template.Label:
        if label == "TOTAL":
            continue
        line, retailer = label.split(" ", 1)
        v = by_line[(by_line.line == line) & (by_line.retailer == retailer)]
        units = None
        if sellout_status.get(retailer, "missing") != "missing":
            units = sellout[(sellout.product_line == line) & (sellout.retailer == retailer)].units.sum()
        rows.append([week, label, len(v), v.rsas_trained.sum(), units])

    # TOTAL: every visit once; sell-out = sum of rows (only when every retailer has reported)
    total_units = None
    if all(s != "missing" for s in sellout_status.values()):
        total_units = sum(r[4] for r in rows)
    else:
        checks.append("TOTAL Sell-out Units left blank until every retailer has reported.")
    rows.append([week, "TOTAL", len(visits), visits.rsas_trained.sum(), total_units])

    # Big week-on-week moves in sell-out: not necessarily wrong, but Laura should know before the client asks
    products = pd.read_csv(DATA / "product_master.csv")
    raw = pd.read_csv(DATA / "retailer_sellout.csv", parse_dates=["week_ending"]).merge(products, on="sku")
    prev = raw[raw.week_ending == pd.Timestamp(saturday - timedelta(days=7))]
    for _, label, _, _, units in rows[:-1]:  # skip TOTAL
        line, retailer = label.split(" ", 1)
        before = prev[(prev.product_line == line) & (prev.retailer == retailer)].units.sum()
        if units is not None and before >= 20 and abs(units / before - 1) > 0.3:
            checks.append(f"Sell-out {label}: {units} vs {before} the week before ({units / before - 1:+.0%}).")

    block = pd.DataFrame(rows, columns=template.columns)
    for col in template.columns[2:]:
        block[col] = pd.to_numeric(block[col]).astype("Int64")

    OUTPUT.mkdir(exist_ok=True)
    block.to_csv(OUTPUT / f"weekly_kpi_{week}.csv", index=False)
    report = (f"# Checks for {week} ({start:%a %d %b} to {saturday:%a %d %b %Y})\n\n"
              f"Generated {datetime.now():%Y-%m-%d %H:%M}\n\n" + "\n".join(f"- {c}" for c in checks) + "\n")
    (OUTPUT / f"checks_{week}.md").write_text(report, encoding="utf-8")

    print(block.to_string(index=False))
    print("\n" + report)


if __name__ == "__main__":
    last_full_week = week_label(date.today() - timedelta(days=7))
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--week", default=last_full_week, help="client week, e.g. 2026-W46 (default: last full week)")
    build(parser.parse_args().week)
