"""The datamart 'schema card' given to the model: tables, columns, meanings, and live data coverage.

The column list is read live from INFORMATION_SCHEMA (so it can't drift from the database);
the business meaning of each column is curated here. Coverage (date range, report count,
substations) is queried live so the agent can be honest about what the data does and doesn't hold.
"""

import time

from .config import Settings
from .db import run_internal_query

# Curated business meaning of each column (unit, grain, caveats).
COLUMN_NOTES = {
    "grid_operations_daily_summary": {
        "_table": "ONE ROW PER DAILY REPORT (one per report_date). Report-level figures exactly as printed on the PDF.",
        "source_file": "Report file name; primary key.",
        "report_date": "Operating day the report covers (DATE).",
        "grid_region": "Grid region, e.g. 'South Dakota Interconnection'.",
        "operator": "Independent system operator that published the report.",
        "balancing_authority": "Balancing authority name.",
        "transmission_operator": "Transmission operator name.",
        "market_zone": "Market zone, e.g. 'Load Zone C'.",
        "generated_at": "When the report was generated (timestamp).",
        "peak_load_mw": "Highest single substation-hour load of the day, in MW.",
        "avg_load_mw": "Average load per substation-hour, in MW.",
        "avg_frequency_hz": "Average grid frequency, Hz (nominal 60 Hz).",
        "avg_reserve_margin_pct": "Average reserve margin, percent.",
        "renewable_share_pct": "Renewable generation as % of total generation for the day.",
        "avg_reliability_index": "Average reliability index (0-100, higher is better).",
        "detail_row_count": "Number of hourly detail rows parsed for the report (expected 96).",
        "loaded_at": "Pipeline audit: when the row was loaded (UTC). Not a business date.",
        "updated_at": "Pipeline audit: when the row was last updated (UTC), or NULL.",
    },
    "grid_operations_detail": {
        "_table": "ONE ROW PER SUBSTATION PER HOUR: 24 hours x 4 substations = 96 rows per report_date.",
        "source_file": "Report file name (part of the key).",
        "reading_time": "Hour of the reading, 'HH:MM' text, '00:00'..'23:00' (part of the key).",
        "substation": "Substation name (part of the key).",
        "report_date": "Operating day (DATE).",
        "reading_datetime": "report_date + reading_time as a DATETIME2.",
        "grid_region": "Grid region (same for every row).",
        "operator": "Independent system operator.",
        "balancing_authority": "Balancing authority.",
        "transmission_operator": "Transmission operator.",
        "market_zone": "Market zone.",
        "generated_at": "When the report was generated.",
        "gen_mw": "Generation at the substation for that hour, MW.",
        "load_mw": "Load (demand) for that hour, MW.",
        "renew_mw": "Renewable generation (wind + solar + other renewables), MW.",
        "wind_mw": "Wind generation, MW.",
        "solar_mw": "Solar generation, MW.",
        "batt_mw": "Battery storage flow, MW: negative = charging, positive = discharging.",
        "freq_hz": "Grid frequency, Hz.",
        "volt_kv": "Voltage, kV (depends on the substation's nominal voltage).",
        "congestion_pct": "Line congestion, percent (0-100).",
        "avail_cap_mw": "Available capacity, MW.",
        "reserve_pct": "Reserve margin, percent.",
        "carbon_g_per_kwh": "Carbon intensity, grams CO2 per kWh.",
        "lmp_usd_per_mwh": "Locational marginal price, USD per MWh.",
        "weather": "Weather: Clear, Partly Cloudy, Cloudy, Rain, Windy, Storm, Snow, Fog.",
        "status": "Equipment status: Operational, Maintenance, Alert.",
        "reliability_index": "Reliability index for that hour (0-100).",
        "renewable_pct_of_gen": "renew_mw / gen_mw * 100 (NULL when gen_mw = 0).",
        "is_operational": "1 when status = 'Operational', else 0 (BIT).",
        "loaded_at": "Pipeline audit timestamp (UTC). Not a business date.",
        "updated_at": "Pipeline audit timestamp (UTC), or NULL.",
    },
}

_CACHE: dict = {}
_TTL_S = 600


def _columns(settings: Settings) -> dict:
    rows = run_internal_query(settings, """
        SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, CHARACTER_MAXIMUM_LENGTH, NUMERIC_PRECISION, NUMERIC_SCALE
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE TABLE_SCHEMA = 'datamart' AND TABLE_NAME IN ('grid_operations_daily_summary', 'grid_operations_detail')
        ORDER BY TABLE_NAME, ORDINAL_POSITION""")
    tables: dict = {}
    for table, column, dtype, length, precision, scale in rows:
        if dtype in ("decimal", "numeric"):
            dtype = f"{dtype}({precision},{scale})"
        elif length and length > 0:
            dtype = f"{dtype}({length})"
        tables.setdefault(table, []).append((column, dtype))
    return tables


def _coverage(settings: Settings) -> dict:
    (reports, first, last), = run_internal_query(settings, """
        SELECT COUNT(*), MIN(report_date), MAX(report_date) FROM datamart.grid_operations_daily_summary""")
    substations = [r[0] for r in run_internal_query(settings, """
        SELECT DISTINCT substation FROM datamart.grid_operations_detail ORDER BY substation""")]
    regions = [r[0] for r in run_internal_query(settings, """
        SELECT DISTINCT grid_region FROM datamart.grid_operations_daily_summary ORDER BY grid_region""")]
    return {"reports": reports, "first": first, "last": last, "substations": substations, "regions": regions}


def build_schema_card(settings: Settings) -> str:
    """Markdown description of the two tables + live coverage. Cached for 10 minutes."""
    cached = _CACHE.get("card")
    if cached and time.time() - cached[0] < _TTL_S:
        return cached[1]

    columns, coverage = _columns(settings), _coverage(settings)
    lines = []
    for table in ("grid_operations_daily_summary", "grid_operations_detail"):
        notes = COLUMN_NOTES[table]
        lines.append(f"### datamart.{table}\n{notes['_table']}")
        for column, dtype in columns.get(table, []):
            lines.append(f"- {column} ({dtype}): {notes.get(column, '')}".rstrip(": "))
        lines.append("")
    lines.append("### Data coverage (live)")
    lines.append(f"- Reports loaded: {coverage['reports']}, report_date from {coverage['first']} to {coverage['last']}.")
    lines.append("- Reports exist for SOME days only (about 8-10 per month historically, then daily). "
                 "A date with no row means there is no report for that day, NOT a zero value.")
    lines.append(f"- Grid regions: {', '.join(coverage['regions']) or 'none'}.")
    lines.append(f"- Substations: {', '.join(coverage['substations']) or 'none'}.")
    card = "\n".join(lines)
    _CACHE["card"] = (time.time(), card)
    return card
