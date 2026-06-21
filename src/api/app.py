import logging

from fastapi import FastAPI
from pydantic import BaseModel

from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse

from src.agent.agent import run_agent, stream_agent


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-8s] %(name)s - %(message)s",
    datefmt="%H:%M:%S",
)

app = FastAPI(title="Website Scraper Agent")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

class ChatRequest(BaseModel):
    url: str
    question: str

@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    async def event_generator():
        try:
            async for chunk in stream_agent(
                question=request.question,
                url=request.url,
            ):
                yield chunk

            yield {
                "event": "done",
                "data": "[DONE]",
            }

        except Exception as error:
            yield {
                "event": "error",
                "data": str(error),
            }

    return EventSourceResponse(event_generator())

@app.get("/")
def health_check():
    return {"status": "ok"}


@app.post("/chat")
async def chat(request: ChatRequest):
    answer = await run_agent(
        question=request.question,
        url=request.url,
    )

    return {
        "answer": answer,
    }
