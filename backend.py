import os
import certifi
import logging
from functools import lru_cache
from dotenv import load_dotenv

load_dotenv()

os.environ["SSL_CERT_FILE"] = certifi.where()
os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()

from typing import TypedDict, Annotated
import operator
import uuid

import psycopg
from psycopg.rows import dict_row

from langgraph.graph import StateGraph, START, END
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.postgres import PostgresSaver
from langchain_core.messages import (
    AnyMessage,
    HumanMessage,
    AIMessage,
    SystemMessage,
)
from langchain_groq import ChatGroq
from tools.tavily_tool import tavily_search
from tools.flight_tool import search_flights


logger = logging.getLogger(__name__)


def get_database_url():
    database_url = os.getenv("DATABASE_URL")

    if not database_url:
        return None

    if "sslmode=" not in database_url:
        separator = "&" if "?" in database_url else "?"
        database_url = f"{database_url}{separator}sslmode=require"

    return database_url

# =========================
# LLM
# =========================

@lru_cache(maxsize=1)
def get_llm():
    api_key = os.getenv("GROQ_API_KEY")
    if not api_key:
        raise ValueError("GROQ_API_KEY is missing. Please add it to your .env file.")

    return ChatGroq(
        model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        api_key=api_key,
        max_tokens=2500,
    )


def truncate_for_prompt(value: str, max_chars: int) -> str:
    """Keep prompts comfortably below Groq's token-per-minute limit."""
    text = str(value or "").strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars].rsplit(" ", 1)[0] + "\n[Additional results omitted]"


# =========================
# State
# =========================

class TravelState(TypedDict):
    messages: Annotated[list[AnyMessage], operator.add]
    user_query: str
    flight_results: str
    hotel_results: str
    itinerary: str
    llm_calls: int


# =========================
# Flight Agent
# =========================

def flight_agent(state: TravelState):
    query = state["user_query"]
    flight_data = search_flights(query)

    return {
        "flight_results": flight_data,
        "messages": [
            AIMessage(content="Flight results fetched.")
        ],
        "llm_calls": state.get("llm_calls", 0)
    }



# =========================
# Hotel Agent
# =========================

def hotel_agent(state: TravelState):
    query = f"Best hotels for {state['user_query']}"
    hotel_results = tavily_search(query)

    return {
        "hotel_results": hotel_results,
        "messages": [
            AIMessage(content="Hotel information fetched.")
        ],
        "llm_calls": state.get("llm_calls", 0)
    }




# =========================
# Itinerary Agent
# =========================

def itinerary_agent(state: TravelState):
    user_query = truncate_for_prompt(state["user_query"], 1500)
    flight_results = truncate_for_prompt(state["flight_results"], 4500)
    hotel_results = truncate_for_prompt(state["hotel_results"], 3500)

    prompt = f"""
Generate the complete final travel response for the user.

User Query:
{user_query}

Flight Results:
{flight_results}

Hotel Results:
{hotel_results}

Use these sections:
1. Trip Summary
2. Flight Information
3. Hotel Suggestions
4. Day-by-Day Itinerary
5. Estimated Budget
6. Final Recommendations

Be clear, practical, budget-aware, and easy to follow. Mention that live flight
status data may not contain ticket prices. Clearly label hotel guidance as not
live-verified if the hotel search result says live search was unavailable.
"""

    response = get_llm().invoke([
        SystemMessage(content="You are an expert travel planner."),
        HumanMessage(content=prompt)
    ])

    return {
        "itinerary": response.content,
        "messages": [response],
        "llm_calls": state.get("llm_calls", 0) + 1
    }



# =========================
# Final Response Agent
# =========================

def final_agent(state: TravelState):
    return {
        # The itinerary agent already produced the formatted final answer. Avoid
        # resending all source material to Groq and consuming the TPM budget twice.
        "messages": [AIMessage(content=state["itinerary"])],
        "llm_calls": state.get("llm_calls", 0),
    }


# =========================
# Build Graph
# =========================

graph = StateGraph(TravelState)

graph.add_node("flight_agent", flight_agent)
graph.add_node("hotel_agent", hotel_agent)
graph.add_node("itinerary_agent", itinerary_agent)
graph.add_node("final_agent", final_agent)

graph.add_edge(START, "flight_agent")
graph.add_edge("flight_agent", "hotel_agent")
graph.add_edge("hotel_agent", "itinerary_agent")
graph.add_edge("itinerary_agent", "final_agent")
graph.add_edge("final_agent", END)


# =========================
# Checkpointer and compiled graph
# =========================

@lru_cache(maxsize=1)
def get_travel_graph():
    """Compile lazily so database problems cannot prevent FastAPI startup."""
    database_url = get_database_url()

    if not database_url:
        logger.warning(
            "DATABASE_URL is not configured; conversation state will be kept in memory."
        )
        return graph.compile(checkpointer=InMemorySaver())

    try:
        connection = psycopg.connect(
            database_url,
            autocommit=True,
            row_factory=dict_row,
            connect_timeout=10,
        )
        checkpointer = PostgresSaver(connection)
        checkpointer.setup()
        return graph.compile(checkpointer=checkpointer)
    except psycopg.Error as exc:
        logger.warning(
            "PostgreSQL is unavailable; using in-memory conversation state: %s",
            exc,
        )
        return graph.compile(checkpointer=InMemorySaver())



# =========================
# Function for FastAPI
# =========================

def run_travel_agent(user_input: str, thread_id: str | None = None):
    if not thread_id:
        thread_id = f"user_{uuid.uuid4().hex}"

    config = {
        "configurable": {
            "thread_id": thread_id
        }
    }

    result = get_travel_graph().invoke(
        {
            "messages": [
                HumanMessage(content=user_input)
            ],
            "user_query": user_input,
            "flight_results": "",
            "hotel_results": "",
            "itinerary": "",
            "llm_calls": 0
        },
        config=config
    )

    final_answer = result["messages"][-1].content

    return {
        "thread_id": thread_id,
        "answer": final_answer,
        "flight_results": result.get("flight_results", ""),
        "hotel_results": result.get("hotel_results", ""),
        "itinerary": result.get("itinerary", ""),
        "llm_calls": result.get("llm_calls", 0),
    }
