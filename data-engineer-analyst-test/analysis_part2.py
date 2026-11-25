"""
Evidence for Part 2 of SOLUTION.md (reproducible numbers).

    python analysis_part2.py

Prints the tables and saves output/xr_megaelectro.png (needs pandas + matplotlib).
"""
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from build_weekly_kpi import week_label

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
OUTPUT = HERE / "output"

stores = pd.read_csv(DATA / "store_master.csv")
products = pd.read_csv(DATA / "product_master.csv")


# ---------------------------------------------------------------- 2A: XR sell-out at MegaElectro
MISSING_IN_W46 = ["ME-012", "ME-021", "ME-055"]          # not in MegaElectro's week-46 file
STOCK_OR_DEMO = ["ME-011", "ME-041", "ME-031", "ME-051"]  # Vista 2 stock-out / broken XR demo (field notes)
GROUPS = ["Other 9 stores", "4 stores: no stock / broken demo", "3 stores missing from the file"]


def group(code):
    if code in MISSING_IN_W46:
        return GROUPS[2]
    if code in STOCK_OR_DEMO:
        return GROUPS[1]
    return GROUPS[0]


sellout = pd.read_csv(DATA / "retailer_sellout.csv", parse_dates=["week_ending"]).merge(products, on="sku")
xr = sellout[(sellout.retailer == "MEGAELECTRO") & (sellout.product_line == "XR")].copy()
xr["group"] = xr.retailer_store_code.map(group)
weeks = sorted(xr.week_ending.unique())
table = (xr.pivot_table(index="group", columns="week_ending", values="units", aggfunc="sum")
           .reindex(index=GROUPS, columns=weeks).fillna(0).astype(int))
table.columns = [week_label(pd.Timestamp(w).date())[-3:] for w in weeks]  # client week, e.g. W46
table.loc["TOTAL"] = table.sum()
table["avg W41-W44"] = table[["W41", "W42", "W43", "W44"]].mean(axis=1).round(1)
print("XR sell-out units at MegaElectro by store group\n")
print(table.to_string(), "\n")

stores_w46 = (xr[xr.week_ending >= weeks[-2]]
              .merge(stores[["retailer_store_code", "store_name"]], on="retailer_store_code")
              .pivot_table(index="store_name", columns="week_ending", values="units", aggfunc="sum"))
stores_w46.columns = ["W45", "W46"]
print(stores_w46.loc[stores.loc[stores.retailer_store_code.isin(STOCK_OR_DEMO + MISSING_IN_W46), "store_name"]]
      .to_string(na_rep="-"), "\n")

# Chart: stacked bars, one segment per store group
colors = ["#257de0", "#f47230", "#00b894"]
weeks_shown = table.columns[:-1]
fig, ax = plt.subplots(figsize=(8, 4.2), dpi=150)
bottom = pd.Series(0, index=weeks_shown)
for name, color in zip(GROUPS, colors):
    values = table.loc[name, weeks_shown]
    ax.bar(weeks_shown, values, bottom=bottom, color=color, width=0.62, label=name, edgecolor="white", linewidth=1.5)
    bottom += values
missing_last_week = table.loc[GROUPS[2], "W45"]
ax.bar(["W46"], [missing_last_week], bottom=[bottom["W46"]], width=0.62, fill=False,
       edgecolor=colors[2], linestyle="--", linewidth=1.2)
ax.annotate(f"Not in file\n({missing_last_week} units in W45)", xy=(7, bottom["W46"] + missing_last_week / 2),
            xytext=(7.45, bottom["W46"] + missing_last_week / 2), va="center", fontsize=8, color="#444444")
for week in ["W45", "W46"]:
    ax.text(week, bottom[week] + 2, str(bottom[week]),
            ha="center", fontsize=9, color="#222222", fontweight="bold")
ax.set_title("XR sell-out at MegaElectro, units per week", loc="left", fontsize=11, color="#222222")
ax.spines[["top", "right", "left"]].set_visible(False)
ax.tick_params(colors="#555555", labelsize=9, length=0)
ax.yaxis.grid(True, color="#e7e7e7", linewidth=0.8)
ax.set_axisbelow(True)
ax.set_xlim(-0.6, 8.6)
ax.legend(frameon=False, fontsize=8, loc="upper left")
fig.text(0.01, 0.01, "W45 = holiday bundle launch. Source: retailer_sellout.csv", fontsize=7, color="#777777")
fig.tight_layout()
OUTPUT.mkdir(exist_ok=True)
fig.savefig(OUTPUT / "xr_megaelectro.png")
print(f"Chart saved to {OUTPUT / 'xr_megaelectro.png'}\n")


# ---------------------------------------------------------------- 2C: visits without training
visits = pd.read_csv(DATA / "field_visits.csv", parse_dates=["checkin_at", "checkout_at", "synced_at"])
visits = visits[visits.rep_code.str.match(r"^R\d+$") & (visits.status == "COMPLETED")]
visits = (visits.sort_values("synced_at")
                .drop_duplicates(["rep_code", "store_code", "checkin_at", "checkout_at"]))
visits = visits[(visits.checkin_at >= "2026-09-20") & (visits.checkin_at < "2026-11-15")]  # client W39-W46

per_store = (visits.groupby("store_code")
                   .agg(visits=("visit_id", "count"), rsas_trained=("rsas_trained", "sum"))
                   .reset_index().merge(stores[["store_code", "store_name", "tier"]], on="store_code"))
print("Share of visits with nobody trained:", f"{(visits.rsas_trained == 0).mean():.0%}")
print("Stores with 2 or fewer RSAs trained in 8 weeks:\n")
print(per_store[per_store.rsas_trained <= 2].sort_values("rsas_trained").to_string(index=False))
