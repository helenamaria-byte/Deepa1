"""Vercel entry point: serves the FastAPI dashboard."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.app.main import app  # noqa: E402,F401
