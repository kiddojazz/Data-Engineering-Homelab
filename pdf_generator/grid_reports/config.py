"""Static pools and distribution parameters for the synthetic grid report generator."""

REPORT_HOURS = 24
NUM_SUBSTATIONS = 4

# Suffixes used to turn a Faker company name into something that reads like a
# real grid operator / balancing authority / ISO.
OPERATOR_SUFFIXES = [
    "Independent System Operator",
    "Regional Transmission Organization",
    "Power Authority",
    "Electric Reliability Council",
    "Transmission Corp.",
]

BALANCING_AUTHORITY_SUFFIXES = [
    "Balancing Authority",
    "Regional Balancing Authority",
]

MARKET_ZONE_PREFIXES = ["Zone", "Sub-Zone", "Load Zone", "Hub"]

SUBSTATION_TYPES = [
    "Substation",
    "Switching Station",
    "Transmission Hub",
    "Interconnection Point",
]

NOMINAL_VOLTAGES_KV = [69, 115, 138, 230, 345, 500]

FUEL_TYPES = [
    "Natural Gas",
    "Nuclear",
    "Coal",
    "Hydro",
    "Wind",
    "Solar",
    "Battery Storage",
    "Other",
]

WEATHER_CONDITIONS = [
    "Clear",
    "Partly Cloudy",
    "Cloudy",
    "Rain",
    "Windy",
    "Storm",
    "Snow",
    "Fog",
]

# weighted so "Operational" dominates, with occasional maintenance/alerts
EQUIPMENT_STATUSES = ["Operational", "Maintenance", "Alert"]
EQUIPMENT_STATUS_WEIGHTS = [0.90, 0.07, 0.03]

# Diurnal load shape: base load (MW) + amplitude * daily sine curve, peaking
# in early evening (~hour 18) with a smaller morning ramp.
BASE_LOAD_MW = 1800
LOAD_AMPLITUDE_MW = 650
LOAD_PEAK_HOUR = 18
LOAD_NOISE_STD_MW = 35

# Solar output: bell curve centered at solar noon, zero at night.
SOLAR_PEAK_MW = 420
SOLAR_PEAK_HOUR = 13
SOLAR_WIDTH_HOURS = 3.2

# Wind output: noisier, weakly time-correlated (slightly higher overnight).
WIND_BASE_MW = 180
WIND_NOISE_STD_MW = 70

OTHER_RENEWABLE_MW = 15  # small constant hydro/biomass-style contribution

RESERVE_MARGIN_TARGET_PCT = 15.0

FREQUENCY_NOMINAL_HZ = 60.0
FREQUENCY_STD_HZ = 0.015

VOLTAGE_STD_PCT = 0.6  # +/- % noise around each substation's nominal kV

CARBON_INTENSITY_BASE = 320  # gCO2/kWh at fully fossil-heavy mix
CARBON_INTENSITY_RENEWABLE_CREDIT = 260  # reduction as renewable share -> 1.0

LMP_BASE_USD_PER_MWH = 28.0
LMP_LOAD_SENSITIVITY = 0.028  # $/MWh per MW of load above base

RELIABILITY_INDEX_BASE = 99.5
RELIABILITY_INDEX_NOISE = 0.4
