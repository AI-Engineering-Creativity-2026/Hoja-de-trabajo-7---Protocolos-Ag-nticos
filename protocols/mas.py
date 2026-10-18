"""Supervisor central y tres workers; toda integración se ejecuta por MCP."""
from __future__ import annotations

from contextlib import AsyncExitStack
import datetime as dt
from urllib.parse import urlparse

import httpx2

from agents import Agent, Runner, RunConfig, ModelSettings
from agents.mcp import MCPServerStreamableHttp, create_static_tool_filter
from shared.parachute import agent_model, agent_model_settings


class MultiAgentSystem:
    def __init__(self, mcp_url: str, http_client_factory=None):
        self.url = mcp_url
        if http_client_factory is None and urlparse(mcp_url).hostname in {"127.0.0.1", "localhost", "::1"}:
            http_client_factory = lambda **kwargs: httpx2.AsyncClient(trust_env=False, **kwargs)
        self.http_client_factory = http_client_factory
        self.stack = AsyncExitStack()

    async def start(self):
        model = agent_model()
        settings = agent_model_settings() or ModelSettings()
        workers = []
        for role, tool, description in [
            ("FAQWorker", "search_knowledge_base", "Responde consultas usando únicamente las FAQs devueltas; si no hay evidencia, admite que no puedes responder."),
            ("WeatherWorker", "weather_tool", "Consulta el clima de la fecha YYYY-MM-DD. Devuelve el reporte exacto sin inventar datos."),
            ("CalendarWorker", "schedule_tool", "Agenda una cita tentativa. Devuelve el JSON exacto. Nunca digas confirmada; respeta scheduled=false y error."),
        ]:
            server = await self.stack.enter_async_context(MCPServerStreamableHttp(
                name=role, params={"url": self.url, "timeout": 90,
                                   **({"httpx_client_factory": self.http_client_factory} if self.http_client_factory else {})},
                cache_tools_list=True, client_session_timeout_seconds=90,
                tool_filter=create_static_tool_filter(allowed_tool_names=[tool]),
            ))
            workers.append(Agent(name=role, instructions=f"ROLE={role}\n{description}",
                                 model=model, model_settings=settings, mcp_servers=[server]))
        self.supervisor = Agent(
            name="SupervisorCentral", model=model, model_settings=settings,
            instructions=("ROLE=SupervisorCentral\nEres el asistente de Parachute S.A. Responde en español. "
                          "Usa consultar_faq para toda pregunta de FAQs; no uses conocimiento externo. "
                          "Para agendar llama consultar_clima primero; si no hay error y es IDEAL o MARGINAL, "
                          "llama calendarizar_cita. Si es NO SEGURO / PROHIBIDO, rechaza. "
                          "Solo una respuesta scheduled=true permite afirmar que existe una cita tentativa, "
                          "pendiente de confirmación del instructor. MARGINAL por viento: solo tándem experimentado. "
                          "Pide fecha si falta. Acepta solo YYYY-MM-DD en los próximos 16 días incluyendo hoy. "
                          f"Hoy es {dt.date.today().isoformat()}."),
            tools=[worker.as_tool(tool_name=name, tool_description=desc) for worker, name, desc in zip(
                workers, ["consultar_faq", "consultar_clima", "calendarizar_cita"],
                ["Consulta FAQs oficiales", "Consulta clima y seguridad", "Agenda tentativamente reconsultando clima"],
            )],
        )

    async def stop(self):
        await self.stack.aclose()

    async def invoke(self, history: list[dict], text: str) -> tuple[str, list[dict]]:
        result = await Runner.run(self.supervisor, history + [{"role": "user", "content": text}],
                                  max_turns=16, run_config=RunConfig(tracing_disabled=True))
        return str(result.final_output), result.to_input_list()
