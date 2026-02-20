"""Address Validator – FastAPI application entry point."""

from fastapi import FastAPI

from routers import parse, standardize, web

app = FastAPI(
    title="Address Validator",
    description="Parse and standardize US addresses per USPS Publication 28.",
    version="1.0.0",
)

app.include_router(parse.router)
app.include_router(standardize.router)
app.include_router(web.router)
