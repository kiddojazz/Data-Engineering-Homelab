import json

from langchain_core.messages import AIMessage, ToolMessage

from agent.guardrails import unverified_figures, withheld_answer


def _result(columns, rows):
    return ToolMessage(content=json.dumps({"row_count": len(rows), "columns": columns, "rows": rows}),
                       tool_call_id="1")


CALL = AIMessage(content="", tool_calls=[{"name": "query_datamart", "id": "1",
                                          "args": {"sql": "SELECT TOP (3) report_date, peak_load_mw FROM x"}}])
RESULT = _result(["report_date", "peak_load_mw", "renewable_share"],
                 [["2026-10-02", 702.7, 0.2551], ["2026-10-01", 697.6, 0.2449]])
MESSAGES = [CALL, RESULT]


def test_figures_from_results_are_verified():
    answer = "The highest peak load was *702.7 MW* on 2026-10-02, followed by 697.6 MW on 2026-10-01."
    assert unverified_figures(answer, MESSAGES, "what was the peak?") == []


def test_rounding_and_percentages_are_accepted():
    answer = "Peak load was about 703 MW (renewable share 25.5%)."
    assert unverified_figures(answer, MESSAGES, "peak?") == []


def test_invented_numbers_and_dates_are_flagged():
    answer = "Peak load was 815.2 MW on 2026-09-30, up 4.8% from the week before."
    problems = unverified_figures(answer, MESSAGES, "peak?")
    assert "815.2" in problems and "2026-09-30" in problems and "4.8" in problems


def test_numbers_from_the_question_and_list_numbering_are_fine():
    answer = "Days in 2026 above 650 MW:\n1. 2026-10-02: 702.7 MW\n2. 2026-10-01: 697.6 MW"
    assert unverified_figures(answer, MESSAGES, "Which days in 2026 had load above 650 MW?") == []


def test_small_counts_must_come_from_results_too():
    alerts = _result(["substation", "alert_hours"], [["Johnsonland", 9], ["Robinsonshire", 8]])
    assert unverified_figures("Johnsonland had 9 alert hours, Robinsonshire 8.", [CALL, alerts], "alerts?") == []
    assert unverified_figures("Johnsonland had 7 alert hours.", [CALL, alerts], "alerts?") == ["7"]


def test_answer_without_any_query_is_flagged():
    problems = unverified_figures("Average LMP was 31.42 USD/MWh.", [], "average LMP?")
    assert problems == ["31.42"]


def test_failed_queries_are_not_evidence():
    failed = ToolMessage(content="QUERY FAILED: error 207: Invalid column name 'x' 999.9", tool_call_id="1")
    assert unverified_figures("It was 999.9 MW.", [CALL, failed], "peak?") == ["999.9"]


def test_withheld_answer_shows_raw_rows_honestly():
    text = withheld_answer(MESSAGES)
    assert "couldn't fully verify" in text and "702.7" in text


def test_input_guard_treats_reading_rows_as_data_question():
    # Red-team D6: "show me every single row" was misclassified as write_request.
    from agent.prompts import INPUT_GUARD_PROMPT

    assert "Reading rows is never a write_request" in INPUT_GUARD_PROMPT
