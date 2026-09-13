import logging
import operator
import os
import uuid
from functools import lru_cache
from typing import Annotated, Literal, TypedDict

import certifi
import psycopg
from dotenv import load_dotenv
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row

from guardrails import validate_travel_request
from mcp_client import call_mcp_tool

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

logger = logging.getLogger(__name__)

AgentName = Literal["flight", "hotel", "weather", "itinerary"]
WorkflowStatus = Literal[
    "planning",
    "approval_required",
    "approved",
    "rejected",
    "completed",
    "guardrail_rejected",
]


class TravelState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str
    guardrail: dict
    completed_agents: list[AgentName]
    supervisor_trace: list[str]
    next_step: str
    flight_results: str
    hotel_results: str
    weather_results: str
    draft_itinerary: str
    final_answer: str
    human_feedback: str
    revision_count: int
    status: WorkflowStatus
    llm_calls: int


def get_database_url() -> str | None:
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        return None
    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        return f"{database_url}{separator}sslmode=require"
    return database_url


@lru_cache(maxsize=1)
def get_llm() -> ChatGroq:
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY is missing. Add it to your .env file.")
    return ChatGroq(
        model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        api_key=api_key,
        max_tokens=2_500,
    )


def truncate_for_prompt(value: object, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    shortened = text[:max_chars].rsplit(" ", 1)[0]
    return shortened + "\n[Additional source data omitted]"


def input_guardrail(state: TravelState) -> dict:
    result = validate_travel_request(state["user_query"])
    if result.allowed:
        return {
            "guardrail": result.to_dict(),
            "status": "planning",
            "supervisor_trace": ["guardrail:allowed"],
        }
    return {
        "guardrail": result.to_dict(),
        "status": "guardrail_rejected",
        "final_answer": result.message,
        "supervisor_trace": [f"guardrail:rejected:{result.code}"],
    }


def route_after_guardrail(state: TravelState) -> str:
    return "supervisor" if state["guardrail"].get("allowed") else END


def supervisor(state: TravelState) -> dict:
    completed = set(state.get("completed_agents", []))
    status = state.get("status", "planning")

    if status in {"guardrail_rejected", "rejected", "completed"}:
        next_step = END
    elif "flight" not in completed:
        next_step = "flight_agent"
    elif "hotel" not in completed:
        next_step = "hotel_agent"
    elif "weather" not in completed:
        next_step = "weather_agent"
    elif "itinerary" not in completed:
        next_step = "itinerary_agent"
    elif status != "approved":
        next_step = "approval_gate"
    else:
        next_step = "final_agent"

    return {
        "next_step": next_step,
        "supervisor_trace": [*state.get("supervisor_trace", []), f"supervisor:{next_step}"],
    }


def route_supervisor(state: TravelState) -> str:
    return state["next_step"]


def _mark_complete(state: TravelState, agent: AgentName) -> list[AgentName]:
    completed = list(state.get("completed_agents", []))
    if agent not in completed:
        completed.append(agent)
    return completed


def flight_agent(state: TravelState) -> dict:
    try:
        results = call_mcp_tool("search_flights", {"query": state["user_query"], "limit": 5})
    except Exception as exc:
        logger.warning("Flight MCP tool unavailable: %s", exc)
        results = "Live flight data is currently unavailable."
    return {
        "flight_results": str(results),
        "completed_agents": _mark_complete(state, "flight"),
        "messages": [AIMessage(content="Flight research completed.")],
    }


def hotel_agent(state: TravelState) -> dict:
    try:
        results = call_mcp_tool(
            "search_hotels",
            {"query": f"Best hotels for {state['user_query']}"},
        )
    except Exception as exc:
        logger.warning("Hotel MCP tool unavailable: %s", exc)
        results = "Live hotel research is currently unavailable."
    return {
        "hotel_results": str(results),
        "completed_agents": _mark_complete(state, "hotel"),
        "messages": [AIMessage(content="Hotel research completed.")],
    }


def weather_agent(state: TravelState) -> dict:
    try:
        results = call_mcp_tool("destination_weather", {"query": state["user_query"]})
    except Exception as exc:
        logger.warning("Weather MCP tool unavailable: %s", exc)
        results = "Live destination weather is currently unavailable."
    return {
        "weather_results": str(results),
        "completed_agents": _mark_complete(state, "weather"),
        "messages": [AIMessage(content="Weather research completed.")],
    }


def itinerary_agent(state: TravelState) -> dict:
    feedback = truncate_for_prompt(state.get("human_feedback", ""), 1_000)
    revision_instruction = (
        f"\nReviewer feedback to apply:\n{feedback}\n" if feedback else ""
    )
    prompt = f"""
Prepare a decision-ready travel plan for the request below.

Request:
{truncate_for_prompt(state['user_query'], 1_500)}

Flight research:
{truncate_for_prompt(state['flight_results'], 3_500)}

Hotel research:
{truncate_for_prompt(state['hotel_results'], 3_000)}

Weather research:
{truncate_for_prompt(state['weather_results'], 1_500)}
{revision_instruction}
Use these sections exactly:
1. Trip summary
2. Flights
3. Hotels
4. Day-by-day itinerary
5. Budget
6. Practical notes

Write plainly and specifically. Separate verified live data from estimates. Do
not invent prices, availability, booking confirmations, or source links. State
important assumptions in one short list. Keep the plan under 1,200 words.
"""
    response = get_llm().invoke(
        [
            SystemMessage(
                content=(
                    "You are a precise travel planner. Produce useful professional prose, "
                    "not promotional copy."
                )
            ),
            HumanMessage(content=prompt),
        ]
    )
    return {
        "draft_itinerary": response.content,
        "completed_agents": _mark_complete(state, "itinerary"),
        "human_feedback": "",
        "status": "approval_required",
        "llm_calls": state.get("llm_calls", 0) + 1,
        "messages": [AIMessage(content="Draft itinerary prepared for review.")],
    }


def approval_gate(state: TravelState) -> dict:
    decision = interrupt(
        {
            "kind": "trip_plan_approval",
            "question": "Approve this itinerary, request a revision, or reject it.",
            "draft": state["draft_itinerary"],
            "revision_count": state.get("revision_count", 0),
            "allowed_actions": ["approve", "revise", "reject"],
        }
    )
    action = decision.get("action") if isinstance(decision, dict) else None
    feedback = str(decision.get("feedback", "")).strip() if isinstance(decision, dict) else ""

    if action == "approve":
        return {"status": "approved", "human_feedback": ""}
    if action == "revise":
        completed = [name for name in state.get("completed_agents", []) if name != "itinerary"]
        return {
            "status": "planning",
            "completed_agents": completed,
            "human_feedback": feedback or "Improve clarity and practicality.",
            "revision_count": state.get("revision_count", 0) + 1,
        }
    return {
        "status": "rejected",
        "final_answer": "The draft was rejected. No itinerary was approved.",
        "human_feedback": feedback,
    }


def final_agent(state: TravelState) -> dict:
    return {
        "status": "completed",
        "final_answer": state["draft_itinerary"],
        "messages": [AIMessage(content=state["draft_itinerary"])],
    }


def build_graph(checkpointer):
    builder = StateGraph(TravelState)
    builder.add_node("input_guardrail", input_guardrail)
    builder.add_node("supervisor", supervisor)
    builder.add_node("flight_agent", flight_agent)
    builder.add_node("hotel_agent", hotel_agent)
    builder.add_node("weather_agent", weather_agent)
    builder.add_node("itinerary_agent", itinerary_agent)
    builder.add_node("approval_gate", approval_gate)
    builder.add_node("final_agent", final_agent)
    builder.add_edge(START, "input_guardrail")
    builder.add_conditional_edges("input_guardrail", route_after_guardrail)
    builder.add_conditional_edges(
        "supervisor",
        route_supervisor,
        {
            "flight_agent": "flight_agent",
            "hotel_agent": "hotel_agent",
            "weather_agent": "weather_agent",
            "itinerary_agent": "itinerary_agent",
            "approval_gate": "approval_gate",
            "final_agent": "final_agent",
            END: END,
        },
    )
    builder.add_edge("flight_agent", "supervisor")
    builder.add_edge("hotel_agent", "supervisor")
    builder.add_edge("weather_agent", "supervisor")
    builder.add_edge("itinerary_agent", "supervisor")
    builder.add_edge("approval_gate", "supervisor")
    builder.add_edge("final_agent", END)
    return builder.compile(checkpointer=checkpointer)


@lru_cache(maxsize=1)
def get_travel_graph():
    database_url = get_database_url()
    if not database_url:
        logger.warning("DATABASE_URL is not set; using in-memory HITL checkpoints.")
        return build_graph(InMemorySaver())
    try:
        connection = psycopg.connect(
            database_url,
            autocommit=True,
            row_factory=dict_row,
            connect_timeout=10,
        )
        checkpointer = PostgresSaver(connection)
        checkpointer.setup()
        return build_graph(checkpointer)
    except psycopg.Error as exc:
        logger.warning("PostgreSQL unavailable; using in-memory HITL checkpoints: %s", exc)
        return build_graph(InMemorySaver())


def _initial_state(user_input: str) -> TravelState:
    return {
        "messages": [HumanMessage(content=user_input)],
        "user_query": user_input,
        "guardrail": {},
        "completed_agents": [],
        "supervisor_trace": [],
        "next_step": "",
        "flight_results": "",
        "hotel_results": "",
        "weather_results": "",
        "draft_itinerary": "",
        "final_answer": "",
        "human_feedback": "",
        "revision_count": 0,
        "status": "planning",
        "llm_calls": 0,
    }


def _interrupt_payload(result: dict) -> dict | None:
    interruptions = result.get("__interrupt__", ())
    if not interruptions:
        return None
    return interruptions[0].value


def _public_result(result: dict, thread_id: str) -> dict:
    approval = _interrupt_payload(result)
    status = "approval_required" if approval else result.get("status", "completed")
    answer = result.get("final_answer") or result.get("draft_itinerary", "")
    return {
        "thread_id": thread_id,
        "status": status,
        "answer": answer,
        "approval": approval,
        "guardrail": result.get("guardrail", {}),
        "flight_results": result.get("flight_results", ""),
        "hotel_results": result.get("hotel_results", ""),
        "weather_results": result.get("weather_results", ""),
        "itinerary": result.get("draft_itinerary", ""),
        "supervisor_trace": result.get("supervisor_trace", []),
        "revision_count": result.get("revision_count", 0),
        "llm_calls": result.get("llm_calls", 0),
    }


def start_travel_plan(user_input: str, thread_id: str | None = None) -> dict:
    thread_id = thread_id or f"trip_{uuid.uuid4().hex}"
    config = {"configurable": {"thread_id": thread_id}}
    result = get_travel_graph().invoke(_initial_state(user_input), config=config)
    return _public_result(result, thread_id)


def resume_travel_plan(thread_id: str, action: str, feedback: str = "") -> dict:
    config = {"configurable": {"thread_id": thread_id}}
    result = get_travel_graph().invoke(
        Command(resume={"action": action, "feedback": feedback}),
        config=config,
    )
    return _public_result(result, thread_id)


def run_travel_agent(user_input: str, thread_id: str | None = None) -> dict:
    """Backward-compatible entry point; returns when human approval is required."""
    return start_travel_plan(user_input, thread_id)
