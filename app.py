import logging
from pathlib import Path
from typing import Literal

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from backend import resume_travel_plan, start_travel_plan

BASE_DIR = Path(__file__).resolve().parent
logger = logging.getLogger(__name__)

app = FastAPI(
    title="TripMate",
    description="Supervised LangGraph travel planning with MCP tools and human approval.",
    version="3.0.0",
)
app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


class TravelRequest(BaseModel):
    message: str = Field(max_length=2_000)
    thread_id: str | None = None


class ApprovalRequest(BaseModel):
    thread_id: str = Field(min_length=1, max_length=200)
    action: Literal["approve", "revise", "reject"]
    feedback: str = Field(default="", max_length=1_000)


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(request=request, name="index.html", context={})


@app.post("/api/travel")
async def travel_planner(request_data: TravelRequest):
    message = request_data.message.strip()
    if not message:
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": "Enter a travel request."},
        )

    try:
        result = await run_in_threadpool(
            start_travel_plan,
            message,
            request_data.thread_id,
        )
    except Exception:
        logger.exception("Travel workflow failed")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "The travel plan could not be generated. Check the server logs.",
            },
        )

    if result["status"] == "guardrail_rejected":
        return JSONResponse(
            status_code=400,
            content={"success": False, "error": result["answer"], **result},
        )
    return JSONResponse(content={"success": True, **result})


@app.post("/api/travel/approval")
async def travel_approval(request_data: ApprovalRequest):
    try:
        result = await run_in_threadpool(
            resume_travel_plan,
            request_data.thread_id,
            request_data.action,
            request_data.feedback.strip(),
        )
    except Exception:
        logger.exception("Travel approval failed")
        return JSONResponse(
            status_code=500,
            content={
                "success": False,
                "error": "The review could not be applied. Start a new plan and try again.",
            },
        )
    return JSONResponse(content={"success": True, **result})


@app.get("/health")
async def health_check():
    return {
        "status": "ok",
        "workflow": "guardrails -> supervisor -> specialists -> human approval",
    }


@app.get("/favicon.ico")
async def favicon():
    return JSONResponse(content={})


if __name__ == "__main__":
    uvicorn.run("app:app", host="127.0.0.1", port=8000, reload=True)
