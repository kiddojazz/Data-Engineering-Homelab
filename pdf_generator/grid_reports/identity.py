"""The fictitious utility every report belongs to.

Generated once from a fixed seed so the whole archive (historical backfill and
daily runs) reads as one utility's history. Seed 42 reproduces the original
project's identity: "South Dakota Interconnection".
"""

import random
from datetime import datetime

from faker import Faker

from . import data_generator

IDENTITY_SEED = 42


def build_identity(seed: int = IDENTITY_SEED) -> tuple[dict, list[dict]]:
    """Returns (metadata_template, substations). Seeding order matches the original
    generate_historical_reports.py, so the same seed yields the same utility."""
    random.seed(seed)
    Faker.seed(seed)
    fake = Faker()
    metadata_template = data_generator.generate_report_metadata(fake, datetime.now())
    substations = data_generator.generate_substations(fake)
    return metadata_template, substations


def metadata_for(template: dict, report_date: datetime, generated_at: datetime) -> dict:
    metadata = dict(template)
    metadata["report_date"] = report_date.date()
    metadata["generated_at"] = generated_at
    return metadata
