from fastapi import FastAPI
from pydantic import BaseModel

from src.agent.agent import run_agent


app = FastAPI(title="Website Scraper Agent")


class ChatRequest(BaseModel):
    url: str
    question: str


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
