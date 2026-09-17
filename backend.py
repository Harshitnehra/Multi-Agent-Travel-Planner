import logging
import operator
import re
import uuid
from functools import lru_cache
from typing import Annotated, Literal, TypedDict

import psycopg
from langchain_core.messages import AIMessage, AnyMessage, HumanMessage, SystemMessage
from langchain_groq import ChatGroq
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import Command, interrupt
from psycopg.rows import dict_row

from config import configure_transport_security, get_settings
from domain import ItineraryGeneration, fallback_trip_plan
from guardrails import validate_travel_request
from mcp_client import call_mcp_tool

configure_transport_security()

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
    structured_plan: dict
    final_answer: str
    human_feedback: str
    revision_count: int
    status: WorkflowStatus
    llm_calls: int


def get_database_url() -> str | None:
    return get_settings().postgres_checkpoint_url()


@lru_cache(maxsize=1)
def get_llm() -> ChatGroq:
    settings = get_settings()
    if not settings.groq_api_key:
        raise ValueError("GROQ_API_KEY is missing. Add it to your .env file.")
    return ChatGroq(
        model=settings.groq_model,
        api_key=settings.groq_api_key,
        # The response contains both the rendered itinerary and the complete
        # structured TripPlan. 2,500 tokens truncated valid tool-call JSON for
        # detailed five-day plans and surfaced as an HTTP 500.
        max_tokens=6_000,
    )


def truncate_for_prompt(value: object, max_chars: int) -> str:
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    shortened = text[:max_chars].rsplit(" ", 1)[0]
    return shortened + "\n[Additional source data omitted]"


def strip_structured_plan_section(markdown: str) -> str:
    """Return human-facing prose without schema dumps or internal labels."""
    original = markdown or ""
    marker = re.search(
        r"(?im)^\s{0,3}(?:#{1,6}\s*)?(?:\*{1,2}|_{1,2})?\s*"
        r"structured\s+trip\s+plan\b.*$",
        original,
    )
    readable = original[: marker.start()] if marker else original
    readable = re.sub(r"(?is)```\s*json\s*.*?```", "", readable)
    readable = re.sub(r"(?im)\bsource_verified\b", "Source checked", readable)
    readable = re.sub(r"(?im)\bnull\b", "To be confirmed", readable)
    readable = re.sub(r"(?i)\|\s*true\s*\|", "| Yes |", readable)
    readable = re.sub(r"(?i)\|\s*false\s*\|", "| No |", readable)
    readable = re.sub(
        r"(?im)^\s*\*\*(trip summary|flights|hotels|day-by-day itinerary|"
        r"budget|practical notes)\*\*\s*$",
        lambda match: f"## {match.group(1)}",
        readable,
    )
    readable = re.sub(r"\n\s*(?:---|\*\*\*|___)\s*$", "", readable).rstrip()
    return readable or "# Travel plan\n\nA readable itinerary is not available yet."


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

Populate both fields required by the response tool schema. In the trip_plan field,
use null for unknown dates, times, flight numbers, prices, addresses, coordinates,
and timezones. A research result is not a booking: keep every suggested flight and
hotel status as "suggested". Set source_verified only when the supplied research
directly supports that exact field. Add every detail required for future booking
or monitoring to missing_information.

The rendered_itinerary field must contain only the six readable sections above.
Never include JSON, a JSON code fence, internal schema fields, or a section named
"Structured trip plan" in rendered_itinerary. Put all machine-readable data only
in the trip_plan field. In the readable itinerary, write "To be confirmed" instead
of null, and never show internal names such as source_verified or missing_information.
"""
    messages = [
        SystemMessage(
            content=(
                "You are a precise travel planner. Produce useful professional prose, "
                "not promotional copy. Never fabricate booking or monitoring data."
            )
        ),
        HumanMessage(content=prompt),
    ]
    generation = _generate_structured_itinerary(messages, state["user_query"])
    return {
        "draft_itinerary": generation.rendered_itinerary,
        "structured_plan": generation.trip_plan.model_dump(mode="json"),
        "completed_agents": _mark_complete(state, "itinerary"),
        "human_feedback": "",
        "status": "approval_required",
        "llm_calls": state.get("llm_calls", 0) + 1,
        "messages": [AIMessage(content="Draft itinerary prepared for review.")],
    }


def _message_text(message: object) -> str:
    content = getattr(message, "content", message)
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, dict) and isinstance(block.get("text"), str):
                parts.append(block["text"])
            elif isinstance(block, str):
                parts.append(block)
        return "\n".join(parts).strip()
    return str(content or "").strip()


def _generate_structured_itinerary(
    messages: list[AnyMessage], original_request: str
) -> ItineraryGeneration:
    """Generate one validated plan, preserving readable output on parse failure."""
    llm = get_llm()
    try:
        result = llm.with_structured_output(
            ItineraryGeneration,
            method="function_calling",
            include_raw=True,
        ).invoke(messages)
        if isinstance(result, ItineraryGeneration):
            return result.model_copy(
                update={
                    "rendered_itinerary": strip_structured_plan_section(
                        result.rendered_itinerary
                    ),
                    "trip_plan": result.trip_plan.model_copy(
                        update={"original_request": original_request}
                    )
                }
            )

        if isinstance(result, dict):
            parsed = result.get("parsed")
            if parsed is not None:
                generation = (
                    parsed
                    if isinstance(parsed, ItineraryGeneration)
                    else ItineraryGeneration.model_validate(parsed)
                )
                return generation.model_copy(
                    update={
                        "rendered_itinerary": strip_structured_plan_section(
                            generation.rendered_itinerary
                        ),
                        "trip_plan": generation.trip_plan.model_copy(
                            update={"original_request": original_request}
                        )
                    }
                )

            raw_text = _message_text(result.get("raw"))
            if raw_text:
                logger.warning("Structured itinerary parsing failed; using safe fallback.")
                return ItineraryGeneration(
                    rendered_itinerary=strip_structured_plan_section(raw_text),
                    trip_plan=fallback_trip_plan(original_request),
                )
    except Exception as exc:
        logger.warning("Structured itinerary generation failed: %s", exc)

    try:
        response = llm.invoke(messages)
        readable = _message_text(response)
    except Exception as exc:
        logger.warning("Readable itinerary fallback failed: %s", exc)
        readable = ""
    if not readable:
        plan = fallback_trip_plan(original_request)
        origin = plan.origin.name if plan.origin else "your origin"
        destination = plan.destination.name if plan.destination else "your destination"
        readable = (
            "# Travel plan draft\n\n"
            f"TripMate saved your request for a trip from **{origin}** to "
            f"**{destination}**, but the AI planning provider is temporarily "
            "unavailable.\n\n"
            "## What is saved\n\n"
            f"- Request: {original_request}\n"
            "- Booking status: no flight or hotel has been booked\n"
            "- Payment status: no payment has been created\n\n"
            "## Details still required\n\n"
            "- Exact travel dates and traveler count\n"
            "- Flight and hotel selections\n"
            "- Sightseeing preferences and budget\n\n"
            "Request a revision when the provider is available to generate the "
            "complete researched itinerary."
        )
    return ItineraryGeneration(
        rendered_itinerary=strip_structured_plan_section(readable),
        trip_plan=fallback_trip_plan(original_request),
    )


def approval_gate(state: TravelState) -> dict:
    decision = interrupt(
        {
            "kind": "trip_plan_approval",
            "question": "Approve this itinerary, request a revision, or reject it.",
            "draft": state["draft_itinerary"],
            "structured_plan": state.get("structured_plan", {}),
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
        "structured_plan": {},
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
        "structured_plan": result.get("structured_plan", {}),
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
