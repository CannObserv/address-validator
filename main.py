"""Address Validator – FastAPI application entry point."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routers import parse, standardize, web

app = FastAPI(
    title="Address Validator",
    description="Parse and standardize US addresses per USPS Publication 28.",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["*"],
)

app.include_router(parse.router)
app.include_router(standardize.router)
app.include_router(web.router)
