"""ASGI entry point for production servers:  uvicorn backend.asgi:app --host 0.0.0.0 --port $PORT"""
from backend.api import create_app

app = create_app()
