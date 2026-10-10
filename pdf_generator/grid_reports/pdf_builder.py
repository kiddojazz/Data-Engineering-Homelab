"""Renders the ISO Daily Market & Grid Operations Report as a landscape PDF."""

from pathlib import Path

import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import landscape, letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

PAGE_SIZE = landscape(letter)
MARGIN = 0.4 * inch

TABLE_COLUMNS = [
    ("timestamp", "Time", 0.55 * inch),
    ("substation", "Substation", 1.05 * inch),
    ("generation_mw", "Gen\n(MW)", 0.5 * inch),
    ("load_demand_mw", "Load\n(MW)", 0.5 * inch),
    ("renewable_generation_mw", "Renew\n(MW)", 0.52 * inch),
    ("wind_generation_mw", "Wind\n(MW)", 0.48 * inch),
    ("solar_generation_mw", "Solar\n(MW)", 0.48 * inch),
    ("battery_storage_mw", "Batt\n(MW)", 0.48 * inch),
    ("frequency_hz", "Freq\n(Hz)", 0.48 * inch),
    ("voltage_kv", "Volt\n(kV)", 0.46 * inch),
    ("line_congestion_pct", "Cong\n(%)", 0.46 * inch),
    ("available_capacity_mw", "Avail Cap\n(MW)", 0.6 * inch),
    ("reserve_margin_pct", "Reserve\n(%)", 0.5 * inch),
    ("carbon_intensity", "Carbon\n(g/kWh)", 0.56 * inch),
    ("lmp_usd_per_mwh", "LMP\n($/MWh)", 0.56 * inch),
    ("weather_conditions", "Weather", 0.6 * inch),
    ("equipment_status", "Status", 0.5 * inch),
    ("reliability_index", "Reliability", 0.55 * inch),
]


def _format_cell(column: str, value) -> str:
    if column == "timestamp":
        return value.strftime("%H:%M")
    return str(value)


def _build_header_footer(metadata: dict):
    def _draw(canvas, doc):
        canvas.saveState()
        width, height = PAGE_SIZE

        canvas.setFont("Helvetica-Bold", 13)
        canvas.drawString(MARGIN, height - 0.35 * inch, metadata["report_title"])
        canvas.setFont("Helvetica", 9)
        canvas.drawString(MARGIN, height - 0.55 * inch, metadata["operator"])
        canvas.drawRightString(
            width - MARGIN,
            height - 0.35 * inch,
            f"Report Date: {metadata['report_date'].strftime('%Y-%m-%d')}",
        )
        canvas.drawRightString(
            width - MARGIN,
            height - 0.55 * inch,
            f"Grid Region: {metadata['grid_region']}",
        )
        canvas.line(MARGIN, height - 0.62 * inch, width - MARGIN, height - 0.62 * inch)

        canvas.setFont("Helvetica", 7)
        canvas.drawString(
            MARGIN,
            0.3 * inch,
            "This report contains synthetically generated data for testing purposes only "
            "and does not represent actual grid operations.",
        )
        canvas.drawRightString(width - MARGIN, 0.3 * inch, f"Page {doc.page}")
        canvas.drawCentredString(
            width / 2,
            0.3 * inch,
            f"Generated {metadata['generated_at'].strftime('%Y-%m-%d %H:%M:%S')}",
        )
        canvas.restoreState()

    return _draw


def build_pdf(metadata: dict, substations: list[dict], df: pd.DataFrame, output_path: Path) -> None:
    styles = getSampleStyleSheet()
    body_style = ParagraphStyle("body", parent=styles["Normal"], fontSize=8.5, leading=11)

    doc = SimpleDocTemplate(
        str(output_path),
        pagesize=PAGE_SIZE,
        leftMargin=MARGIN,
        rightMargin=MARGIN,
        topMargin=0.85 * inch,
        bottomMargin=0.55 * inch,
        title=metadata["report_title"],
    )

    story = []

    meta_rows = [
        ["Balancing Authority", metadata["balancing_authority"], "Transmission Operator", metadata["transmission_operator"]],
        ["Market Zone", metadata["market_zone"], "Reporting Period", f"{metadata['report_date']} 00:00 - 23:59"],
    ]
    meta_table = Table(meta_rows, colWidths=[1.5 * inch, 3.3 * inch, 1.7 * inch, 3.3 * inch])
    meta_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f2f2f2")),
        ("BACKGROUND", (2, 0), (2, -1), colors.HexColor("#f2f2f2")),
    ]))
    story.append(meta_table)
    story.append(Spacer(1, 10))

    total_gen = df["generation_mw"].sum()
    total_renew = df["renewable_generation_mw"].sum()
    renewable_pct = (total_renew / total_gen * 100) if total_gen else 0
    summary_rows = [
        ["Peak Load (MW)", f"{df['load_demand_mw'].max():.1f}", "Avg Load (MW)", f"{df['load_demand_mw'].mean():.1f}"],
        ["Avg Frequency (Hz)", f"{df['frequency_hz'].mean():.3f}", "Avg Reserve Margin (%)", f"{df['reserve_margin_pct'].mean():.1f}"],
        ["Renewable Share (%)", f"{renewable_pct:.1f}", "Avg Reliability Index", f"{df['reliability_index'].mean():.1f}"],
    ]
    summary_table = Table(summary_rows, colWidths=[1.7 * inch, 1.5 * inch, 1.9 * inch, 1.5 * inch])
    summary_table.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (0, -1), "Helvetica-Bold"),
        ("FONTNAME", (2, 0), (2, -1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#cccccc")),
    ]))
    story.append(Paragraph("<b>Grid Performance Summary</b>", body_style))
    story.append(Spacer(1, 4))
    story.append(summary_table)
    story.append(Spacer(1, 12))

    header_style = ParagraphStyle(
        "th", parent=styles["Normal"], fontSize=6.8, leading=8,
        textColor=colors.white, alignment=1, fontName="Helvetica-Bold",
    )
    header_row = [Paragraph(label.replace("\n", "<br/>"), header_style) for _, label, _ in TABLE_COLUMNS]
    data_rows = [
        [_format_cell(col, row[col]) for col, _, _ in TABLE_COLUMNS]
        for _, row in df.iterrows()
    ]
    table_data = [header_row] + data_rows
    col_widths = [w for _, _, w in TABLE_COLUMNS]

    op_table = Table(table_data, colWidths=col_widths, repeatRows=1)
    op_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f3a5f")),
        ("FONTSIZE", (0, 1), (-1, -1), 6.5),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f7fa")]),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#cccccc")),
        ("ALIGN", (2, 1), (-1, -1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 2),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 2),
    ]))
    story.append(op_table)

    story.append(Spacer(1, 10))
    story.append(Paragraph(
        "<i>Note: All figures are synthetically generated for software testing and do not reflect "
        "actual grid conditions or NERC/FERC-reportable data.</i>",
        body_style,
    ))

    draw = _build_header_footer(metadata)
    doc.build(story, onFirstPage=draw, onLaterPages=draw)
