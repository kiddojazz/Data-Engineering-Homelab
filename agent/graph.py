"""The grid data agent as a LangGraph state machine.

    START -> input_guard --data_question--> agent <-> tools (query_datamart only)
                 |                            |
                 |                            v
                 |                       grounding_check --unverified, retry left--> agent
                 |                            |
                 +--greeting/refusal--> END   +--verified / withheld--> END

- input_guard: classifies the message; only data questions reach the SQL agent.
- agent: the strong model with exactly ONE tool bound (query_datamart); tool budget per question.
- tools: runs the SQL through the SQL guard, then the read-only database layer.
- grounding_check: every number/date in the answer must come from a query result; otherwise
  one retry with feedback, then an honest "can't verify" answer with the raw result.
"""

import logging
from typing import Annotated, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage, trim_messages
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from . import guardrails
from .config import Settings, get_settings
from .prompts import AGENT_SYSTEM_PROMPT, GROUNDING_RETRY_PROMPT, HELP_TEXT, MODEL_UNAVAILABLE, REFUSALS
from .schema_context import build_schema_card
from .tools import make_query_tool

log = logging.getLogger(__name__)
MAX_HISTORY_MESSAGES = 40       # messages sent to the model per turn (the full thread stays in memory)


class AgentState(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    question: str
    category: str
    turn_start: int            # index of this turn's question in `messages`
    grounding_retries: int
    verified: bool
    unverified: list


def _turn_messages(state: AgentState) -> list:
    return state["messages"][state.get("turn_start", 0):]


def _tool_calls_this_turn(state: AgentState) -> int:
    return sum(len(m.tool_calls or []) for m in _turn_messages(state) if isinstance(m, AIMessage))


def build_graph(settings: Settings | None = None, chat_llm=None, checkpointer=None, schema_card_fn=None):
    """Builds the compiled graph. The model and the schema card are injectable for tests."""
    settings = settings or get_settings()
    if chat_llm is None:
        from .llm import chat_model
        chat_llm = chat_model(settings)
    schema_card_fn = schema_card_fn or (lambda: build_schema_card(settings))

    query_tool = make_query_tool(settings)
    agent_llm = chat_llm.bind_tools([query_tool], tool_choice="auto", parallel_tool_calls=False)

    # ---------------------------------------------------------------- nodes ----
    def input_guard(state: AgentState) -> dict:
        question = state["question"].strip()
        turn_start = len(state["messages"]) - 1
        if len(question) > settings.max_question_chars:
            return {"category": "too_long", "turn_start": turn_start}
        if not question:
            return {"category": "greeting_or_help", "turn_start": turn_start}
        try:
            category = guardrails.classify_input(chat_llm, question, state["messages"][:-1]).category
        except Exception as exc:  # noqa: BLE001
            if guardrails.is_content_filter_error(exc):
                log.warning("Azure content filter blocked the message (prompt shield)")
                category = "prompt_injection"
            else:  # fail towards the guarded SQL path, never towards free text
                log.warning("Input guard unavailable (%s); treating the message as a data question", str(exc)[:160])
                category = "data_question"
        return {"category": category, "turn_start": turn_start, "grounding_retries": 0}

    def respond_without_data(state: AgentState) -> dict:
        category = state["category"]
        text = HELP_TEXT if category == "greeting_or_help" else REFUSALS.get(category, REFUSALS["out_of_scope"])
        return {"messages": [AIMessage(content=text)], "verified": True, "unverified": []}

    def agent(state: AgentState) -> dict:
        system = SystemMessage(content=AGENT_SYSTEM_PROMPT.format(schema_card=schema_card_fn()))
        # Long conversations (Slack threads) keep only the most recent messages, starting on a user turn,
        # so tool calls and their results are never split.
        history = trim_messages(state["messages"], max_tokens=MAX_HISTORY_MESSAGES, token_counter=len,
                                strategy="last", start_on="human", include_system=False, allow_partial=False)
        try:
            if _tool_calls_this_turn(state) >= settings.max_tool_calls:
                note = SystemMessage(content=(
                    "You have used the maximum number of queries for this question. Do not call tools. "
                    "Answer now using only the query results above, and say clearly if they are not enough."))
                response = chat_llm.invoke([system, *history, note])
            else:
                response = agent_llm.invoke([system, *history])
        except Exception as exc:  # noqa: BLE001 - never crash the conversation on a model error
            if guardrails.is_content_filter_error(exc):
                log.warning("Azure content filter blocked the agent request")
                response = AIMessage(content=REFUSALS["prompt_injection"])
            else:
                log.error("Language model call failed: %s", str(exc)[:200])
                response = AIMessage(content=MODEL_UNAVAILABLE)
        return {"messages": [response]}

    tools_node = ToolNode([query_tool], handle_tool_errors=True)

    def grounding_check(state: AgentState) -> dict:
        answer = state["messages"][-1]
        text = answer.content if isinstance(answer.content, str) else str(answer.content)
        problems = guardrails.unverified_figures(
            text, _turn_messages(state) + _earlier_tool_results(state), state["question"] + "\n" + schema_card_fn())
        if not problems:
            return {"verified": True, "unverified": []}
        retries = state.get("grounding_retries", 0)
        if retries < settings.max_grounding_retries:
            log.info("Grounding check: unverified figures %s; asking the model to fix them", problems)
            return {"messages": [HumanMessage(content=GROUNDING_RETRY_PROMPT.format(unverified=", ".join(problems)),
                                              name="grounding_check")],
                    "grounding_retries": retries + 1, "verified": False, "unverified": problems}
        log.warning("Grounding check failed after retries: %s; withholding figures", problems)
        return {"messages": [AIMessage(content=guardrails.withheld_answer(_turn_messages(state)))],
                "verified": False, "unverified": problems}

    def _earlier_tool_results(state: AgentState) -> list:
        return [m for m in state["messages"][: state.get("turn_start", 0)] if isinstance(m, ToolMessage)]

    # -------------------------------------------------------------- routing ----
    def after_guard(state: AgentState) -> str:
        return "agent" if state["category"] == "data_question" else "respond_without_data"

    def after_agent(state: AgentState) -> str:
        last = state["messages"][-1]
        return "tools" if isinstance(last, AIMessage) and last.tool_calls else "grounding_check"

    def after_grounding(state: AgentState) -> str:
        last = state["messages"][-1]
        return "agent" if isinstance(last, HumanMessage) and last.name == "grounding_check" else END

    graph = StateGraph(AgentState)
    graph.add_node("input_guard", input_guard)
    graph.add_node("respond_without_data", respond_without_data)
    graph.add_node("agent", agent)
    graph.add_node("tools", tools_node)
    graph.add_node("grounding_check", grounding_check)
    graph.add_edge(START, "input_guard")
    graph.add_conditional_edges("input_guard", after_guard, ["agent", "respond_without_data"])
    graph.add_edge("respond_without_data", END)
    graph.add_conditional_edges("agent", after_agent, ["tools", "grounding_check"])
    graph.add_edge("tools", "agent")
    graph.add_conditional_edges("grounding_check", after_grounding, ["agent", END])
    return graph.compile(checkpointer=checkpointer if checkpointer is not None else MemorySaver())


_GRAPH = None


def ask(question: str, session_id: str = "default", user_id: str | None = None, graph=None) -> dict:
    """Runs one question in a conversation thread. Returns the answer plus the evidence used."""
    from .observability import flush, tracing_config

    global _GRAPH
    if graph is None:
        _GRAPH = _GRAPH or build_graph()
        graph = _GRAPH
    config = {"configurable": {"thread_id": session_id}, "recursion_limit": 40,
              "run_name": "gridiq-question", **tracing_config(user_id, session_id)}
    state = graph.invoke({"messages": [HumanMessage(content=question)], "question": question}, config=config)
    flush()

    turn = state["messages"][state.get("turn_start", 0):]
    queries = [call["args"].get("sql") for m in turn if isinstance(m, AIMessage) for call in (m.tool_calls or [])]
    return {
        "answer": state["messages"][-1].content,
        "category": state.get("category"),
        "verified": state.get("verified", False),
        "unverified": state.get("unverified", []),
        "sql_queries": [q for q in queries if q],
    }
