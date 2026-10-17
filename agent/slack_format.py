"""Turns an agent result into Slack Block Kit: mrkdwn answer + a footer saying how it was grounded."""

import re

SECTION_LIMIT = 2900            # Slack's section text limit is 3000 characters
SHOW_SQL = re.compile(r"\b(show|include|with|give me|what was|see)\b.{0,20}\b(sql|query|queries)\b", re.IGNORECASE)


def to_mrkdwn(text: str) -> str:
    """Model markdown -> Slack mrkdwn (bold, headings, bullets, links)."""
    text = re.sub(r"\*\*(.+?)\*\*", r"*\1*", text)                    # **bold** -> *bold*
    text = re.sub(r"(?m)^#{1,6}\s*(.+)$", r"*\1*", text)               # headings -> bold line
    text = re.sub(r"(?m)^(\s*)[-*]\s+", r"\1• ", text)                 # bullets
    text = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"<\2|\1>", text)  # links
    return text.strip()


def _chunks(text: str, limit: int = SECTION_LIMIT) -> list:
    parts, current = [], ""
    for paragraph in text.split("\n"):
        if len(current) + len(paragraph) + 1 > limit and current:
            parts.append(current)
            current = ""
        while len(paragraph) > limit:
            parts.append(paragraph[:limit])
            paragraph = paragraph[limit:]
        current = f"{current}\n{paragraph}" if current else paragraph
    if current:
        parts.append(current)
    return parts or [" "]


def wants_sql(question: str) -> bool:
    return bool(SHOW_SQL.search(question))


def footer(result: dict) -> str:
    queries = len(result.get("sql_queries") or [])
    category = result.get("category")
    if category != "data_question":
        return ":information_source: Answered without querying the datamart"
    if not result.get("verified"):
        return ":warning: Some figures couldn't be verified against the query results, so they were withheld"
    if queries == 0:
        return ":information_source: No query was needed"
    return f":white_check_mark: Every figure verified against the datamart · {queries} SQL quer{'y' if queries == 1 else 'ies'}"


def answer_blocks(result: dict, question: str) -> tuple:
    """Returns (fallback_text, blocks)."""
    answer = to_mrkdwn(str(result.get("answer", "")))
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": chunk}} for chunk in _chunks(answer)]
    if wants_sql(question) and result.get("sql_queries"):
        for i, sql in enumerate(result["sql_queries"], 1):
            code = f"*SQL {i}*\n```{sql.strip()}```"
            blocks += [{"type": "section", "text": {"type": "mrkdwn", "text": c}} for c in _chunks(code)]
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": footer(result)}]})
    return answer[:3000], blocks[:50]          # Slack allows at most 50 blocks per message
