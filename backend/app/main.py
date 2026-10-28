"""Standalone ASGI entry point; embedded hosts import memory_slice.web instead."""
import os
from memory_slice.web import create_app, cancellable_recall

app = create_app(seed=os.environ.get("SEED_DEMO", "true").lower() == "true")
