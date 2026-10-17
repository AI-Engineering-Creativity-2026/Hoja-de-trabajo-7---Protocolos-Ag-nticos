"""Adaptadores de negocio; solo el proceso MCP importa las integraciones."""
from __future__ import annotations

import json
import os
import sys
import threading
from pathlib import Path

from shared import parachute as business

_SCHEDULE_LOCK = threading.Lock()


class Services:
    def __init__(self):
        business.APPOINTMENTS_PATH = Path(os.getenv("APPOINTMENTS_PATH", "data/citas.json")).resolve()

    def search(self, query: str) -> list[dict]:
        if not query.strip():
            raise ValueError("La consulta no puede estar vacía.")
        source = str(business.HDT4_SRC_PATH)
        if source not in sys.path:
            sys.path.insert(0, source)
        from database import search_knowledge_base
        return search_knowledge_base(query=query)

    def weather(self, date: str) -> dict:
        return json.loads(business.weather_tool(date))

    def schedule(self, date: str) -> dict:
        with _SCHEDULE_LOCK:
            return json.loads(business.schedule_tool(date))
