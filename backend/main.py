from dotenv import load_dotenv
load_dotenv()

import logging

logging.basicConfig(
    force=True,
    level=logging.DEBUG,
    format="%(asctime)s %(levelname)-8s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
# multipart 的 DEBUG 日志极其冗长（每次上传几十行），压制到 WARNING
logging.getLogger("multipart").setLevel(logging.WARNING)

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from backend.controllers import (
    file_controller,
    qa_controller,
    quiz_controller,
    wiki_controller,
)
from backend.dao.database import init_db

logger = logging.getLogger(__name__)

app = FastAPI(title="CoReader", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled %s on %s %s", type(exc).__name__, request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content={"detail": f"{type(exc).__name__}: {exc}"},
    )


app.include_router(file_controller.router)
app.include_router(wiki_controller.router)
app.include_router(qa_controller.router)
app.include_router(quiz_controller.router)

init_db()


@app.get("/api/health")
async def health():
    from backend.config import QWEN_MODEL
    return {"status": "ok", "model": QWEN_MODEL}
