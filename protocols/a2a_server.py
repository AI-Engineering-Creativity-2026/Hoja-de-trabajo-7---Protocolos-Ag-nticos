"""Fachada A2A 0.3 JSON-RPC: descubrimiento, message/send, tasks/get y tasks/cancel.

Implementación directa del binding; no requiere el paquete opcional a2a-sdk.
"""
from __future__ import annotations

import argparse
import asyncio
from collections import OrderedDict
from contextlib import asynccontextmanager
import datetime as dt
import logging
import os
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from typing import Literal
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
import uvicorn

logger = logging.getLogger("parachute.a2a")


class TextPart(BaseModel):
    kind: Literal["text"]
    text: str = Field(min_length=1, max_length=16000)
    metadata: dict | None = None


class UserMessage(BaseModel):
    model_config = ConfigDict(extra="ignore")
    kind: Literal["message"]
    role: Literal["user"]
    messageId: str = Field(min_length=1, max_length=128)
    contextId: str | None = Field(default=None, max_length=128)
    taskId: str | None = None
    parts: list[TextPart] = Field(min_length=1, max_length=16)


def agent_card(url: str) -> dict:
    return {
        "protocolVersion": "0.3.0", "name": "Parachute S.A.", "version": "1.0.0",
        "description": "FAQs y citas con validación meteorológica.",
        "url": url, "preferredTransport": "JSONRPC",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text/plain"], "defaultOutputModes": ["text/plain"],
        "skills": [{"id": name, "name": title, "description": description,
                    "tags": tags, "examples": examples}
                   for name, title, description, tags, examples in [
                       ("faq", "Preguntas frecuentes", "Responde con las FAQs oficiales.", ["faq", "rag"], ["¿Dónde se ubica la zona de salto?"]),
                       ("weather", "Clima", "Consulta hasta 16 días y evalúa seguridad.", ["weather"], ["Consulta el clima para YYYY-MM-DD"]),
                       ("appointments", "Citas", "Agenda tentativamente tras reconsultar clima.", ["calendar"], ["Quiero agendar una cita para YYYY-MM-DD"]),
                   ]],
    }


class RpcError(Exception):
    def __init__(self, code: int, message: str):
        self.code, self.message = code, message


def create_app(runtime, url="http://127.0.0.1:8000/"):
    tasks: OrderedDict[str, dict] = OrderedDict()
    sessions: OrderedDict[str, dict] = OrderedDict()

    @asynccontextmanager
    async def lifespan(app):
        try:
            await runtime.start()
            logger.info("A2A ready protocol=0.3.0 transport=JSONRPC")
            yield
        finally:
            await runtime.stop()

    async def card(request):
        return JSONResponse(agent_card(url))

    async def dispatch(method, params):
        if method in {"tasks/get", "tasks/cancel"}:
            task_id = params.get("id")
            if not isinstance(task_id, str):
                raise RpcError(-32602, "id es obligatorio")
            if task_id not in tasks:
                raise RpcError(-32001, "Task not found")
            if method == "tasks/cancel":
                raise RpcError(-32002, "Task not cancelable")
            task = dict(tasks[task_id])
            length = params.get("historyLength")
            if length is not None:
                if not isinstance(length, int) or isinstance(length, bool) or length < 0:
                    raise RpcError(-32602, "historyLength debe ser un entero no negativo")
                task["history"] = task["history"][-length:] if length else []
            return task
        if method != "message/send":
            raise RpcError(-32601, "Method not found")
        try:
            message = UserMessage.model_validate(params.get("message"))
        except ValidationError:
            raise RpcError(-32602, "Se requiere un Message de usuario con messageId y TextParts válidos")
        if message.taskId:
            # Cada turno crea una tarea; tareas terminadas no se reinician.
            if message.taskId not in tasks:
                raise RpcError(-32001, "Task not found")
            raise RpcError(-32602, "La tarea terminó; envía otro mensaje con el mismo contextId")
        configuration = params.get("configuration", {})
        if not isinstance(configuration, dict):
            raise RpcError(-32602, "configuration debe ser un objeto")
        modes = configuration.get("acceptedOutputModes", ["text/plain"])
        if not isinstance(modes, list) or "text/plain" not in modes:
            raise RpcError(-32005, "Content type not supported")
        context_id = message.contextId or uuid4().hex
        if context_id not in sessions:
            if len(sessions) >= 256:
                idle = next((key for key, value in sessions.items() if not value["lock"].locked()), None)
                if idle is None:
                    raise RpcError(-32603, "Demasiadas sesiones activas")
                sessions.pop(idle)
            sessions[context_id] = {"history": [], "messages": OrderedDict(), "lock": asyncio.Lock()}
        session = sessions[context_id]
        sessions.move_to_end(context_id)
        async with session["lock"]:
            text = "\n".join(part.text for part in message.parts)
            if not text.strip():
                raise RpcError(-32602, "El mensaje no puede estar vacío")
            if message.messageId in session["messages"]:
                previous_text, previous_task = session["messages"][message.messageId]
                if previous_text != text:
                    raise RpcError(-32602, "messageId ya usado con otro contenido")
                return previous_task
            task_id = uuid4().hex
            logger.info("request.start task=%s context=%s", task_id, context_id)
            state = "completed"
            try:
                async with asyncio.timeout(180):
                    answer, history = await runtime.invoke(session["history"], text)
                session["history"] = history
            except Exception:
                logger.exception("request.error task=%s", task_id)
                state, answer = "failed", "No se pudo completar la solicitud. Revisa los logs del servidor."
            response_message = {"kind": "message", "role": "agent", "messageId": uuid4().hex,
                                "contextId": context_id, "taskId": task_id,
                                "parts": [{"kind": "text", "text": answer}]}
            task = {"kind": "task", "id": task_id, "contextId": context_id,
                    "status": {"state": state, "timestamp": dt.datetime.now(dt.timezone.utc).isoformat(),
                               "message": response_message},
                    "history": [message.model_dump(exclude_none=True), response_message]}
            if state == "completed":
                task["artifacts"] = [{"artifactId": uuid4().hex, "name": "respuesta",
                                      "parts": response_message["parts"]}]
            tasks[task_id] = task
            session["messages"][message.messageId] = (text, task)
            while len(tasks) > 512:
                tasks.popitem(last=False)
            while len(session["messages"]) > 32:
                session["messages"].popitem(last=False)
            logger.info("request.end task=%s state=%s", task_id, state)
            return task

    async def rpc(request: Request):
        request_id = None
        try:
            try:
                body = await request.json()
            except ValueError:
                raise RpcError(-32700, "Parse error")
            if not isinstance(body, dict) or body.get("jsonrpc") != "2.0":
                raise RpcError(-32600, "Invalid Request")
            request_id = body.get("id")
            if (not isinstance(request_id, (str, int)) or isinstance(request_id, bool)
                    or not isinstance(body.get("method"), str)):
                request_id = None
                raise RpcError(-32600, "Invalid Request")
            params = body.get("params", {})
            if not isinstance(params, dict):
                raise RpcError(-32602, "Invalid params")
            result = await dispatch(body["method"], params)
            return JSONResponse({"jsonrpc": "2.0", "id": request_id, "result": result})
        except RpcError as exc:
            logger.warning("rpc.error code=%s message=%s", exc.code, exc.message)
            return JSONResponse({"jsonrpc": "2.0", "id": request_id,
                                 "error": {"code": exc.code, "message": exc.message}})

    return Starlette(lifespan=lifespan, routes=[
        Route("/.well-known/agent-card.json", card), Route("/", rpc, methods=["POST"]),
    ])


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--mcp-url", default=os.getenv("MCP_URL", "http://127.0.0.1:8001/mcp"))
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    from protocols.mas import MultiAgentSystem
    runtime = MultiAgentSystem(args.mcp_url)
    app = create_app(runtime, os.getenv("A2A_PUBLIC_URL", f"http://127.0.0.1:{args.port}/"))
    uvicorn.run(app, host="127.0.0.1", port=args.port)


if __name__ == "__main__":
    main()
