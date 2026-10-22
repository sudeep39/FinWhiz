"""ASGI factory used by Uvicorn."""

from .api import create_app


def app():
    return create_app()
