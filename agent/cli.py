"""Ask the agent from the command line.

    python -m agent.cli "What was the peak load on the latest report date?"
    python -m agent.cli --session demo "Which substation had the most Alert hours in September 2026?"
    python -m agent.cli --session demo "And in August?"        # follow-up in the same session
    python -m agent.cli --show-sql "Average LMP by substation in 2026"
"""

import argparse
import logging
import sys

from .graph import ask


def main() -> int:
    parser = argparse.ArgumentParser(description="Ask the grid data agent a question.")
    parser.add_argument("question")
    parser.add_argument("--session", default="cli", help="Conversation id (reuse it for follow-ups).")
    parser.add_argument("--user", default="cli-user")
    parser.add_argument("--show-sql", action="store_true", help="Print the SQL the agent ran.")
    args = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    result = ask(args.question, session_id=args.session, user_id=args.user)
    print(result["answer"])
    print(f"\n[category: {result['category']} | verified: {result['verified']}"
          + (f" | unverified: {', '.join(result['unverified'])}" if result["unverified"] else "") + "]")
    if args.show_sql:
        for i, sql in enumerate(result["sql_queries"], 1):
            print(f"\n-- query {i}\n{sql}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
