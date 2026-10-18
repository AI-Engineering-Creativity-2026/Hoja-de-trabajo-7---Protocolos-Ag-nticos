"""Construye respuestas a partir de los resultados observados de herramientas."""
from __future__ import annotations

from dataclasses import dataclass, field
import json

from agents import RunHooks

NO_ANSWER = "No tengo información suficiente en las FAQs para responder esa consulta."
TOOL_NAMES = {"search_knowledge_base", "weather_tool", "schedule_tool"}


def decode_output(value: object) -> object:
    # Los retornos sin esquema estructurado llegan como bloques de texto MCP.
    if isinstance(value, dict) and value.get("type") == "text":
        return decode_output(value.get("text"))
    if isinstance(value, list) and len(value) == 1 and isinstance(value[0], dict) and value[0].get("type") == "text":
        return decode_output(value[0])
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return None
    # MCP envuelve los retornos tipados dentro de structuredContent.result.
    if isinstance(value, dict) and set(value) == {"result"}:
        return value["result"]
    return value


def faq_answer(value: object) -> str:
    rows = value.get("result", []) if isinstance(value, dict) else value
    if not isinstance(rows, list):
        return NO_ANSWER
    answers = []
    for row in rows:
        if not isinstance(row, dict) or row.get("id") == "METADATA":
            continue
        answer = row.get("respuesta")
        if not isinstance(answer, str) or not answer.strip():
            continue
        # Estos registros describen la pregunta pero no contienen su respuesta.
        if answer.lstrip().casefold().startswith("respuesta detallada para la consulta sobre"):
            continue
        answers.append(answer.strip())
    return "\n\n".join(dict.fromkeys(answers)) or NO_ANSWER


def weather_answer(value: object) -> str:
    if not isinstance(value, dict):
        return "No se pudo obtener información meteorológica verificable."
    if value.get("error"):
        return "No se pudo consultar el clima: " + str(value["error"])
    if not value.get("date") or not value.get("decision"):
        return "No se pudo obtener información meteorológica verificable."
    lines = [f"Clima para {value['date']}. Decisión: {value['decision']}."]
    for key, label, unit in [
        ("temperature_c", "Temperatura", "°C"),
        ("precipitation_mm", "Precipitación", "mm"),
        ("cloud_cover_pct", "Cobertura de nubes", "%"),
        ("visibility_m", "Visibilidad", "m"),
        ("wind_speed_kmh", "Viento", "km/h"),
        ("wind_gust_kmh", "Ráfagas", "km/h"),
    ]:
        if value.get(key) is not None:
            lines.append(f"{label}: {value[key]} {unit}.")
    reasons = value.get("reasons", [])
    if isinstance(reasons, list):
        lines.extend(str(reason) for reason in reasons)
    return "\n".join(lines)


def appointment_answer(value: object) -> str:
    if not isinstance(value, dict):
        return "No se pudo verificar la calendarización de la cita."
    if value.get("scheduled") is True:
        return (f"Cita calendarizada tentativamente para {value.get('date', '')}; "
                "pendiente de confirmación del instructor.")
    return "La cita no fue calendarizada. " + str(value.get("error", "No hay confirmación de la herramienta."))


@dataclass
class TurnEvidence:
    calls: list[tuple[str, object]] = field(default_factory=list)

    def answer(self) -> str:
        renderers = {"search_knowledge_base": faq_answer,
                     "weather_tool": weather_answer, "schedule_tool": appointment_answer}
        parts = [renderers[name](value) for name, value in self.calls if name in renderers]
        return "\n\n".join(dict.fromkeys(parts)) or NO_ANSWER


class EvidenceHooks(RunHooks[TurnEvidence]):
    async def on_tool_end(self, context, agent, tool, result):
        if tool.name in TOOL_NAMES and isinstance(context.context, TurnEvidence):
            context.context.calls.append((tool.name, decode_output(result)))
