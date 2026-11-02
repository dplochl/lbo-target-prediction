"""
Stage 12 — Firm-year risk-set panel with forward-looking positive labels,
per design_memo.md Sections 2, 4 and 6 (v1.5: analysis window FY2012-2025).

Input:  data/interim/stage_11_annual_filers.csv (one row per CIK x fiscal year, FY>=2012)
        data/interim/stage_11b_public_float.csv (EntityPublicFloat per firm-year)
        data/interim/combined_events.csv        (one row per CIK, event flags)
Output: data/processed/firm_year_panel.csv
        data/interim/stage_12_excluded_by_listing_filter.csv (rows the filter removed, for audit)
        data/interim/stage_12_event_window_coverage.csv (per positive event)

Listing filter (two signals, applied per firm-year): EntityPublicFloat > 0, taken
from the same fiscal year's filing (Stage 11b), AND afs is not null; a zero float in a
firm's first listed year (positive float the following fiscal year) is exempt. `afs` itself
is kept as a column for robustness checks and is deliberately NOT used as a size or
listing screen: Non-Accelerated does not mean private/unlisted, and Accelerated and
Smaller-Reporting-Company can apply to the same firm. Caveat: a positive public
float shows the firm has publicly held equity, not that it is exchange-listed
(OTC-quoted firms also report a float).

Labels. For an outcome flag and horizon h (1, 3, 5), a firm-year (cik, FY) is
positive iff the firm's event year Y satisfies  Y-h <= FY <= Y-1.  The event
year itself is excluded from the window because a firm going private in year Y
normally files no 10-K for fiscal year Y; the last 10-K it files is FY Y-1.
event year = year of first_event_date (first proxy / SC 13E-3 filing).

Outcomes (all defined on combined_events.csv):
    pe_main   = sponsor_like_ptp         (main analysis)
    pe_strict = institutional_pe_only    (robustness)
    gp_broad  = completion_status == 'completed' AND form25_present
                (broad going-private, PE and non-PE; the form25 condition is the
                 same acquirer-side-proxy guard already required by the two
                 PE outcomes)

At-risk rows. For every firm that completed a going-private (gp_broad), rows
with FY >= event year are dropped (post-event / event-year rows are not at
risk). Firms whose events did not complete stay in the panel as negatives.
Firms with completion_status == 'uncertain' are kept but flagged, since the
design memo excludes them from the main analysis.

Right-censoring of labels. Events are observed through 2025, so a firm-year's
h-year label is only fully observed when FY + h <= 2025 (`h{h}_observed`).
Rows failing that are still written; downstream code must filter on the flag
before fitting or evaluating.
"""

from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
ANNUAL_PATH = REPO_ROOT / "data" / "interim" / "stage_11_annual_filers.csv"
FLOAT_PATH = REPO_ROOT / "data" / "interim" / "stage_11b_public_float.csv"
EXCLUDED_PATH = REPO_ROOT / "data" / "interim" / "stage_12_excluded_by_listing_filter.csv"
EVENTS_PATH = REPO_ROOT / "data" / "interim" / "combined_events.csv"
PANEL_PATH = REPO_ROOT / "data" / "processed" / "firm_year_panel.csv"
WINDOW_COVERAGE_PATH = REPO_ROOT / "data" / "interim" / "stage_12_event_window_coverage.csv"

PANEL_START_FY = 2012
EVENT_OBSERVED_THROUGH = 2025
HORIZONS = (1, 3, 5)
MAIN_HORIZON = 3


def load_events() -> pd.DataFrame:
    events = pd.read_csv(EVENTS_PATH, dtype={"cik": str})
    events["event_year"] = pd.to_datetime(events["first_event_date"], errors="coerce").dt.year
    events["pe_main"] = events["sponsor_like_ptp"].astype(str) == "True"
    events["pe_strict"] = events["institutional_pe_only"].astype(str) == "True"
    events["gp_broad"] = (events["completion_status"] == "completed") & (events["form25_present"].astype(str) == "True")
    return events[["cik", "event_year", "completion_status", "pe_main", "pe_strict", "gp_broad"]]


def load_annual_with_float() -> pd.DataFrame:
    annual = pd.read_csv(ANNUAL_PATH, dtype={"cik": str})
    public_float = pd.read_csv(FLOAT_PATH, dtype={"cik": str})
    annual = annual.merge(public_float, on=["cik", "fiscal_year"], how="left", validate="one_to_one")
    # A newly public firm's first 10-K reports float 0 (it was not yet listed on the
    # measurement date, the end of its second fiscal quarter) - e.g. Datto FY2020, Weber
    # FY2021, Minerva FY2021. Found because these known positives failed a plain float > 0
    # test. Exempt a zero-float row only when the firm's float is > 0 the next fiscal year;
    # firms with zero float throughout (subsidiary debt-only filers) stay excluded.
    next_float = annual.set_index(["cik", "fiscal_year"])["entity_public_float"]
    keys = list(zip(annual["cik"], annual["fiscal_year"] + 1))
    annual["next_year_float"] = pd.Series(keys).map(next_float).to_numpy()
    annual["first_listing_year_exempt"] = (annual["entity_public_float"] == 0) & (annual["next_year_float"] > 0)
    annual["passes_listing_filter"] = (
        ((annual["entity_public_float"] > 0) | annual["first_listing_year_exempt"]) & annual["afs"].notna()
    )
    return annual.drop(columns=["next_year_float"])


def build_panel(annual: pd.DataFrame) -> pd.DataFrame:
    excluded = annual[~annual["passes_listing_filter"]]
    excluded.to_csv(EXCLUDED_PATH, index=False)
    annual = annual[annual["passes_listing_filter"]]
    events = load_events()
    panel = annual.merge(events, on="cik", how="left")
    for flag in ("pe_main", "pe_strict", "gp_broad"):
        panel[flag] = panel[flag].fillna(False).astype(bool)

    # Post-event rows are not at risk (see module docstring).
    post_event = panel["gp_broad"] & (panel["fiscal_year"] >= panel["event_year"])
    print(f"Dropping {post_event.sum()} post-event/event-year rows of {panel['gp_broad'].sum()} gp_broad firm-year rows "
          f"({panel.loc[post_event, 'cik'].nunique()} firms)")
    panel = panel[~post_event].copy()

    for outcome in ("pe_main", "pe_strict", "gp_broad"):
        for h in HORIZONS:
            in_window = (panel["fiscal_year"] >= panel["event_year"] - h) & (panel["fiscal_year"] <= panel["event_year"] - 1)
            panel[f"y_{outcome}_h{h}"] = (panel[outcome] & in_window).astype(int)
    for h in HORIZONS:
        panel[f"h{h}_observed"] = panel["fiscal_year"] + h <= EVENT_OBSERVED_THROUGH

    panel["sic_num"] = pd.to_numeric(panel["sic"], errors="coerce")
    sic_major = panel["sic_num"] // 100  # 2-digit SIC major group
    panel["fin_sic_60_64"] = sic_major.between(60, 64)
    panel["fin_sic_60_67"] = sic_major.between(60, 67)
    panel = panel.drop(columns=["sic_num"])
    return panel.sort_values(["cik", "fiscal_year"]).reset_index(drop=True)


def window_coverage(panel: pd.DataFrame) -> pd.DataFrame:
    """Per positive event: how many of the fiscal years in [Y-3, Y-1] actually
    exist as panel rows (i.e. FY>=2012 and the firm has a DERA 10-K for it)."""
    events = load_events()
    rows_by_cik = panel.groupby("cik")["fiscal_year"].apply(set).to_dict()
    out = []
    for ev in events[events["pe_main"] | events["pe_strict"] | events["gp_broad"]].itertuples():
        window = range(ev.event_year - MAIN_HORIZON, ev.event_year)
        present = [fy for fy in window if fy in rows_by_cik.get(ev.cik, set())]
        out.append({
            "cik": ev.cik, "event_year": ev.event_year, "pe_main": ev.pe_main, "pe_strict": ev.pe_strict,
            "gp_broad": ev.gp_broad, "usable_window_years": len(present),
        })
    return pd.DataFrame(out)


def report(panel: pd.DataFrame, cov: pd.DataFrame) -> None:
    print(f"\nPanel: {len(panel)} firm-years, {panel['cik'].nunique()} firms, FY {panel['fiscal_year'].min()}-{panel['fiscal_year'].max()}")
    for outcome in ("pe_main", "pe_strict", "gp_broad"):
        sub = cov[cov[outcome]]
        n = len(sub)
        zero = int((sub["usable_window_years"] == 0).sum())
        full = int((sub["usable_window_years"] == MAIN_HORIZON).sum())
        rows = int(panel[f"y_{outcome}_h{MAIN_HORIZON}"].sum())
        print(f"{outcome}: {n} events | zero usable window-years: {zero} ({zero / n:.1%}) | "
              f"full 3-year window: {full} | positive firm-year rows (h=3): {rows} of ceiling {MAIN_HORIZON * n}")
    for h in HORIZONS:
        obs = panel[panel[f"h{h}_observed"]]
        print(f"h={h}: fully-observed rows {len(obs)}; positives pe_main {int(obs[f'y_pe_main_h{h}'].sum())}, "
              f"pe_strict {int(obs[f'y_pe_strict_h{h}'].sum())}, gp_broad {int(obs[f'y_gp_broad_h{h}'].sum())}")


def main():
    annual = load_annual_with_float()
    print(f"Listing filter: {int(annual['passes_listing_filter'].sum())} of {len(annual)} firm-years pass "
          f"(float>0 AND afs notnull); float missing {int(annual['entity_public_float'].isna().sum())}, "
          f"float == 0 {int((annual['entity_public_float'] == 0).sum())} (of which first-listing-year exempt "
          f"{int(annual['first_listing_year_exempt'].sum())}), afs null {int(annual['afs'].isna().sum())}")
    panel = build_panel(annual)
    PANEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    panel.to_csv(PANEL_PATH, index=False)
    cov = window_coverage(panel)
    cov.to_csv(WINDOW_COVERAGE_PATH, index=False)
    report(panel, cov)


if __name__ == "__main__":
    main()
