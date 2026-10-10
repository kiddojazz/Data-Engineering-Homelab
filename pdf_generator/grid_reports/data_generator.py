"""Builds the synthetic ISO Daily Market / Grid Operations dataset."""

import random
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
from faker import Faker

from . import config


def _make_operator_name(fake: Faker, suffix_pool: list[str]) -> str:
    return f"{fake.company().rstrip(',.')} {random.choice(suffix_pool)}"


def generate_report_metadata(fake: Faker, report_date: datetime) -> dict:
    region_name = f"{fake.state()} Interconnection"
    operator = _make_operator_name(fake, config.OPERATOR_SUFFIXES)
    balancing_authority = _make_operator_name(fake, config.BALANCING_AUTHORITY_SUFFIXES)
    transmission_operator = _make_operator_name(fake, ["Transmission Co.", "Grid Services", "Power Delivery"])
    market_zone = f"{random.choice(config.MARKET_ZONE_PREFIXES)} {fake.random_uppercase_letter()}"

    return {
        "report_title": "ISO Daily Market & Grid Operations Report",
        "report_date": report_date.date(),
        "grid_region": region_name,
        "operator": operator,
        "balancing_authority": balancing_authority,
        "transmission_operator": transmission_operator,
        "market_zone": market_zone,
        "generated_at": datetime.now(),
    }


def generate_substations(fake: Faker, n: int = config.NUM_SUBSTATIONS) -> list[dict]:
    substations = []
    for _ in range(n):
        name = f"{fake.city()} {random.choice(config.SUBSTATION_TYPES)}"
        nominal_kv = random.choice(config.NOMINAL_VOLTAGES_KV)
        load_share = random.uniform(0.18, 0.32)
        installed_capacity_mw = round(
            (config.BASE_LOAD_MW + config.LOAD_AMPLITUDE_MW) * load_share * random.uniform(1.15, 1.35), 1
        )
        substations.append({
            "name": name,
            "nominal_kv": nominal_kv,
            "load_share": load_share,
            "installed_capacity_mw": installed_capacity_mw,
        })
    total_share = sum(s["load_share"] for s in substations)
    for s in substations:
        s["load_share"] /= total_share
    return substations


def build_dataframe(metadata: dict, substations: list[dict], seed: int | None = None) -> pd.DataFrame:
    rng = np.random.default_rng(seed)

    hours = np.arange(config.REPORT_HOURS)

    weather_weights = [0.28, 0.18, 0.16, 0.10, 0.12, 0.04, 0.05, 0.07]
    weather_by_hour = rng.choice(config.WEATHER_CONDITIONS, size=config.REPORT_HOURS, p=weather_weights)

    # fixed conventional fuel split for this report (Gas / Nuclear / Coal)
    conv_weights = rng.dirichlet([3, 2, 1])

    rows = []
    report_date = metadata["report_date"]

    for sub in substations:
        sub_base_load = config.BASE_LOAD_MW * sub["load_share"]
        sub_amplitude = config.LOAD_AMPLITUDE_MW * sub["load_share"]
        sub_solar_peak = config.SOLAR_PEAK_MW * sub["load_share"] * 1.4
        sub_wind_base = config.WIND_BASE_MW * sub["load_share"] * 1.4

        for hour in hours:
            timestamp = datetime.combine(report_date, datetime.min.time()) + timedelta(hours=int(hour))
            weather = weather_by_hour[hour]

            load = (
                sub_base_load
                + sub_amplitude * np.cos(2 * np.pi * (hour - config.LOAD_PEAK_HOUR) / 24)
                + rng.normal(0, config.LOAD_NOISE_STD_MW * sub["load_share"])
            )
            load = max(load, sub_base_load * 0.3)

            solar_factor = 0.55 if weather in ("Cloudy", "Storm", "Rain") else (0.8 if weather == "Partly Cloudy" else 1.0)
            solar = sub_solar_peak * solar_factor * np.exp(
                -((hour - config.SOLAR_PEAK_HOUR) ** 2) / (2 * config.SOLAR_WIDTH_HOURS ** 2)
            )
            solar = max(solar, 0)

            wind_factor = 1.6 if weather in ("Windy", "Storm") else 1.0
            wind = max(0.0, sub_wind_base * wind_factor + rng.normal(0, config.WIND_NOISE_STD_MW * sub["load_share"]))

            other_renewable = config.OTHER_RENEWABLE_MW * sub["load_share"]
            renewable = solar + wind + other_renewable

            if 11 <= hour <= 15:
                battery = -abs(rng.normal(15, 8)) * sub["load_share"]
            elif 17 <= hour <= 21:
                battery = abs(rng.normal(20, 10)) * sub["load_share"]
            else:
                battery = rng.normal(0, 4) * sub["load_share"]

            generation = load + rng.normal(5, 8) * sub["load_share"]
            generation = max(generation, renewable)

            installed_capacity = sub["installed_capacity_mw"]
            available_capacity = max(installed_capacity - generation, 0)
            reserve_margin_pct = ((installed_capacity - load) / load) * 100 if load > 0 else 0
            line_congestion_pct = min(100.0, max(0.0, (load / installed_capacity) * 100 + rng.normal(0, 2)))

            renewable_share = min(renewable / generation, 1.0) if generation > 0 else 0
            conventional_mw = max(generation - renewable, 0)
            gas_mw, nuclear_mw, coal_mw = conv_weights * conventional_mw

            if generation > 0:
                fuel_mix = (
                    f"Gas {gas_mw / generation * 100:.0f}% / "
                    f"Nuclear {nuclear_mw / generation * 100:.0f}% / "
                    f"Coal {coal_mw / generation * 100:.0f}% / "
                    f"Wind {wind / generation * 100:.0f}% / "
                    f"Solar {solar / generation * 100:.0f}% / "
                    f"Other {other_renewable / generation * 100:.0f}%"
                )
            else:
                fuel_mix = "N/A"

            carbon_intensity = max(
                config.CARBON_INTENSITY_BASE
                - config.CARBON_INTENSITY_RENEWABLE_CREDIT * renewable_share
                + rng.normal(0, 8),
                20,
            )

            lmp = max(
                config.LMP_BASE_USD_PER_MWH
                + config.LMP_LOAD_SENSITIVITY * (load - sub_base_load)
                + rng.normal(0, 3),
                5,
            )

            frequency = rng.normal(config.FREQUENCY_NOMINAL_HZ, config.FREQUENCY_STD_HZ)
            voltage = sub["nominal_kv"] * (1 + rng.normal(0, config.VOLTAGE_STD_PCT / 100))

            equipment_status = rng.choice(config.EQUIPMENT_STATUSES, p=config.EQUIPMENT_STATUS_WEIGHTS)
            reliability_index = config.RELIABILITY_INDEX_BASE + rng.normal(0, config.RELIABILITY_INDEX_NOISE)
            if equipment_status == "Alert":
                reliability_index -= rng.uniform(3, 8)
            elif equipment_status == "Maintenance":
                reliability_index -= rng.uniform(1, 3)
            reliability_index = min(max(reliability_index, 60), 100)

            rows.append({
                "timestamp": timestamp,
                "grid_region": metadata["grid_region"],
                "balancing_authority": metadata["balancing_authority"],
                "transmission_operator": metadata["transmission_operator"],
                "substation": sub["name"],
                "generation_mw": round(generation, 1),
                "load_demand_mw": round(load, 1),
                "renewable_generation_mw": round(renewable, 1),
                "wind_generation_mw": round(wind, 1),
                "solar_generation_mw": round(solar, 1),
                "battery_storage_mw": round(battery, 1),
                "frequency_hz": round(frequency, 3),
                "voltage_kv": round(voltage, 1),
                "line_congestion_pct": round(line_congestion_pct, 1),
                "available_capacity_mw": round(available_capacity, 1),
                "reserve_margin_pct": round(reserve_margin_pct, 1),
                "carbon_intensity": round(carbon_intensity, 1),
                "lmp_usd_per_mwh": round(lmp, 2),
                "fuel_mix": fuel_mix,
                "weather_conditions": weather,
                "equipment_status": equipment_status,
                "market_zone": metadata["market_zone"],
                "reliability_index": round(reliability_index, 1),
            })

    df = pd.DataFrame(rows)
    df.sort_values(["timestamp", "substation"], inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df
