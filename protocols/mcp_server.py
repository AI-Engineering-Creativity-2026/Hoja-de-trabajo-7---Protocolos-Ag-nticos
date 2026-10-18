"""Un único servidor MCP con Streamable HTTP, herramientas, recurso y prompt."""
from __future__ import annotations

import argparse
import asyncio
import logging
import time
from uuid import uuid4

import uvicorn
from mcp.server import MCPServer

from protocols.services import Services

logger = logging.getLogger("parachute.mcp")


def create_server() -> MCPServer:
    services = Services()
    server = MCPServer("ParachuteTools", version="1.0.0")

    def invoke(name, callback, **kwargs):
        call_id, started = uuid4().hex[:8], time.monotonic()
        logger.info("tool.start name=%s call=%s", name, call_id)
        try:
            result = callback(**kwargs)
            failed = isinstance(result, dict) and "error" in result
            logger.info("tool.end name=%s call=%s outcome=%s duration_ms=%.1f", name, call_id,
                        "rejected" if failed else "ok", (time.monotonic() - started) * 1000)
            return result
        except Exception:
            logger.exception("tool.error name=%s call=%s", name, call_id)
            raise

    async def execute(name, callback, **kwargs):
        return await asyncio.to_thread(invoke, name, callback, **kwargs)

    @server.tool()
    async def search_knowledge_base(query: str) -> list[dict]:
        """Busca FAQs oficiales en PostgreSQL/pgvector. No inventes si devuelve []."""
        return await execute("search_knowledge_base", services.search, query=query)

    @server.tool()
    async def weather_tool(date: str) -> dict:
        """Consulta clima y seguridad para YYYY-MM-DD, dentro de los 16 días incluyendo hoy."""
        return await execute("weather_tool", services.weather, date=date)

    @server.tool()
    async def schedule_tool(date: str) -> dict:
        """Reconsulta clima y persiste una cita tentativa solo con clima IDEAL o MARGINAL."""
        return await execute("schedule_tool", services.schedule, date=date)

    @server.resource("parachute://safety-policy")
    def safety_policy() -> str:
        return ("Viento >28 km/h, ráfagas >35 km/h, lluvia >0 mm o nubes >75%: PROHIBIDO. "
                "Viento 20–28: solo tándem experimentado. Nubes 30–75%: MARGINAL. "
                "Toda cita queda pendiente de confirmación del instructor.")

    @server.prompt()
    def plan_visit(date: str) -> str:
        return f"Consulta weather_tool para {date}; agenda solo si el usuario lo solicita y el clima lo permite."

    return server


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    logger.info("MCP starting transport=streamable-http")
    app = create_server().streamable_http_app(stateless_http=True, json_response=True)
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
