"""Main module of the FastAPI application.

This module sets up the FastAPI application and the middleware to log incoming requests
and unhandled exceptions.
"""

import subprocess
import time
import traceback
import uuid
from contextlib import asynccontextmanager
from typing import Union

from fastapi import FastAPI, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import ValidationError
from starlette.middleware.cors import CORSMiddleware

from app.api.v1.api import api_router
from app.core.config import settings
from app.core.exceptions import NotFoundException, PermissionException, unpack_validation_error
from app.core.logging import logger
from app.db.init_db import init_db
from app.db.session import AsyncSessionLocal
from app.platform.db_sync import sync_platform_components


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Lifespan context manager for startup and shutdown events."""
    # Startup
    async with AsyncSessionLocal() as db:
        if settings.RUN_ALEMBIC_MIGRATIONS:
            logger.info("Running alembic migrations...")
            subprocess.run(["alembic", "upgrade", "head"], check=True)
        if settings.RUN_DB_SYNC:
            await sync_platform_components("app/platform", db)
        await init_db(db)
    yield
    # Shutdown


app = FastAPI(title=settings.PROJECT_NAME, openapi_url="/openapi.json", lifespan=lifespan)

app.include_router(api_router)


@app.middleware("http")
async def add_request_id(request: Request, call_next: callable) -> Response:
    """Middleware to generate and add a request ID to the request for tracing.

    Args:
    ----
        request (Request): The incoming request.
        call_next (callable): The next middleware in the chain.

    Returns:
    -------
        Response: The response to the incoming request.

    """
    request.state.request_id = str(uuid.uuid4())
    return await call_next(request)


@app.middleware("http")
async def log_requests(request: Request, call_next: callable) -> Response:
    """Middleware to log incoming requests.

    Args:
    ----
        request (Request): The incoming request.
        call_next (callable): The next middleware in the chain.

    Returns:
    -------
        Response: The response to the incoming request.

    """
    start_time = time.time()
    response = await call_next(request)
    duration = time.time() - start_time
    logger.info(
        (
            f"Handled request {request.method} {request.url} in {duration:.2f} seconds."
            f"Response code: {response.status_code}"
        )
    )
    return response


@app.middleware("http")
async def exception_logging_middleware(request: Request, call_next: callable) -> Response:
    """Middleware to log unhandled exceptions.

    Args:
    ----
        request (Request): The incoming request.
        call_next (callable): The next middleware in the chain.

    Returns:
    -------
        Response: The response to the incoming request.

    """
    try:
        response = await call_next(request)
        return response
    except Exception as exc:
        logger.error(f"Unhandled exception: {exc}\n{traceback.format_exc()}")
        return JSONResponse(status_code=500, content={"detail": "Internal Server Error"})


@app.exception_handler(RequestValidationError)
@app.exception_handler(ValidationError)
async def validation_exception_handler(
    request: Request, exc: Union[RequestValidationError, ValidationError]
) -> JSONResponse:
    """Exception handler for validation errors that occur during request processing.

    This handler captures exceptions raised due to request data not passing the schema validation.

    It improves the client's ability to understand what part of their request was invalid
    and why, facilitating easier debugging and correction.

    Args:
    ----
        request (Request): The incoming request that triggered the exception.
        exc (Union[RequestValidationError, ValidationError]): The exception object that was raised.
            This can either be a RequestValidationError for request body/schema validation issues,
            or a ValidationError for other data model validations within FastAPI.

    Returns:
    -------
        JSONResponse: A 422 Unprocessable Entity status response that details the validation
            errors. Each error message is a dictionary where the key is the location
            of the validation error in the request, and the value is the associated error message.

    Example of JSON output:
        {
            "errors": [
                {"body.email": "field required"},
                {"body.age": "value is not a valid integer"}
            ]
        }

    """
    error_messages = unpack_validation_error(exc)
    return JSONResponse(status_code=422, content=error_messages)


@app.exception_handler(PermissionException)
async def permission_exception_handler(request: Request, exc: PermissionException) -> JSONResponse:
    """Exception handler for PermissionException.

    Args:
    ----
        request (Request): The incoming request that triggered the exception.
        exc (PermissionException): The exception object that was raised.

    Returns:
    -------
        JSONResponse: A 403 Forbidden status response that details the error message.

    """
    return JSONResponse(status_code=403, content={"detail": str(exc)})


@app.exception_handler(NotFoundException)
async def not_found_exception_handler(request: Request, exc: NotFoundException) -> JSONResponse:
    """Exception handler for NotFoundException.

    Args:
    ----
        request (Request): The incoming request that triggered the exception.
        exc (NotFoundException): The exception object that was raised.

    Returns:
    -------
        JSONResponse: A 404 Not Found status response that details the error message.

    """
    return JSONResponse(status_code=404, content={"detail": str(exc)})


app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://localhost:8080",
        "http://localhost:3000",
        "localhost:5173",
        "app.dev-airweave.ai",
        "app.tst-airweave.ai",
        "app.acc-airweave.ai",
        "app.airweave.ai",
        "docs.airweave.ai",
        "https://docs.airweave.ai",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", response_class=HTMLResponse, include_in_schema=False)
async def show_docs_reference() -> HTMLResponse:
    """Root endpoint to display the API documentation.

    Returns:
    -------
        HTMLResponse: The HTML content to display the API documentation.

    """
    html_content = """
<!DOCTYPE html>
<html>
    <head>
        <title>Airweave API</title>
    </head>
    <body>
        <h1>Welcome to the Airweave API</h1>
        <p>Please visit the <a href="https://docs.airweave.ai">docs</a> for more information.</p>
    </body>
</html>
    """
    return HTMLResponse(content=html_content)
