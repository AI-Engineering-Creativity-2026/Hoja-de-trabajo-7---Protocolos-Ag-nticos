"""Regresiones contra respuestas inventadas fuera de las FAQs recuperadas."""
import asyncio
import json
import re
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from protocols.evidence import EvidenceHooks, NO_ANSWER, TurnEvidence, faq_answer
from protocols.mas import MultiAgentSystem
from shared.parachute import FAQ_PATH


def location_record():
    text = FAQ_PATH.read_text(encoding="utf-8")
    answer = re.search(r"^RESPUESTA: (.+)$", text, re.MULTILINE).group(1)
    return {"id": "FAQ-001", "pregunta": "¿Dónde se ubica la zona de salto?", "respuesta": answer}


class FaqEvidenceTests(unittest.TestCase):
    def test_actual_location_record_requires_abstention(self):
        self.assertEqual(faq_answer({"result": [location_record()]}), NO_ANSWER)

    def test_concrete_answers_are_preserved_verbatim(self):
        answer = "La zona de salto está en el aeródromo indicado en tu reserva."
        self.assertEqual(faq_answer({"result": [{"id": "FAQ-001", "respuesta": answer}]}), answer)

    def test_empty_or_invalid_evidence_requires_abstention(self):
        for value in [None, [], {}, {"result": []}, {"result": "dato inválido"},
                      [{"pregunta": "¿Dónde?"}], [{"respuesta": " "}]]:
            with self.subTest(value=value):
                self.assertEqual(faq_answer(value), NO_ANSWER)

    def test_unrelated_metadata_does_not_supply_an_address(self):
        self.assertEqual(faq_answer({"result": [location_record(),
            {"id": "METADATA", "respuesta": "empresa: Parachute S.A., fecha: 2026-09-29"}]}), NO_ANSWER)


class FaqRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_supervisor_hallucination_is_never_returned_or_remembered(self):
        hooks = EvidenceHooks()
        async def run(*args, context, **kwargs):
            await hooks.on_tool_end(SimpleNamespace(context=context), None,
                SimpleNamespace(name="search_knowledge_base"), json.dumps({"result": [location_record()]}))
            return SimpleNamespace(final_output="Aeródromo Internacional de Santa Isabel, a 5 km del centro.")
        runtime = MultiAgentSystem("http://127.0.0.1:8001/mcp")
        runtime.supervisor = object()
        with patch("protocols.mas.Runner.run", side_effect=run):
            answer, history = await runtime.invoke([], "Donde se ubica l zona del salto?")
        self.assertEqual(answer, NO_ANSWER)
        self.assertEqual(history[-1]["content"], NO_ANSWER)
        self.assertNotIn("Santa Isabel", str(history))

    async def test_model_answer_without_a_tool_result_is_rejected(self):
        runtime = MultiAgentSystem("http://127.0.0.1:8001/mcp")
        runtime.supervisor = object()
        with patch("protocols.mas.Runner.run", new=AsyncMock(return_value=SimpleNamespace(final_output="Dirección inventada"))):
            answer, history = await runtime.invoke([], "¿Dónde es el evento?")
        self.assertEqual(answer, NO_ANSWER)
        self.assertNotIn("Dirección inventada", str(history))

    async def test_mixed_request_preserves_verified_appointment(self):
        hooks = EvidenceHooks()
        async def run(*args, context, **kwargs):
            for name, value in [
                ("search_knowledge_base", {"result": [location_record()]}),
                ("schedule_tool", {"scheduled": True, "date": "2026-10-20"}),
            ]:
                await hooks.on_tool_end(SimpleNamespace(context=context), None, SimpleNamespace(name=name), json.dumps(value))
            return SimpleNamespace(final_output="Ubicación inventada y cita confirmada definitivamente.")
        runtime = MultiAgentSystem("http://127.0.0.1:8001/mcp")
        runtime.supervisor = object()
        with patch("protocols.mas.Runner.run", side_effect=run):
            answer, _ = await runtime.invoke([], "¿Dónde es y puedo agendar para 2026-10-20?")
        self.assertIn(NO_ANSWER, answer)
        self.assertIn("tentativamente para 2026-10-20", answer)
        self.assertNotIn("definitivamente", answer)

    async def test_concurrent_requests_do_not_share_faq_evidence(self):
        hooks = EvidenceHooks()
        async def run(agent, inputs, *, context, **kwargs):
            answer = inputs[-1]["content"]
            await asyncio.sleep(0)
            await hooks.on_tool_end(SimpleNamespace(context=context), None,
                SimpleNamespace(name="search_knowledge_base"), json.dumps({"result": [{"id": "FAQ", "respuesta": answer}]}))
        runtime = MultiAgentSystem("http://127.0.0.1:8001/mcp")
        runtime.supervisor = object()
        with patch("protocols.mas.Runner.run", side_effect=run):
            results = await asyncio.gather(runtime.invoke([], "Primera respuesta"), runtime.invoke([], "Segunda respuesta"))
        self.assertEqual([result[0] for result in results], ["Primera respuesta", "Segunda respuesta"])

    async def test_weather_details_come_only_from_tool_results(self):
        evidence = TurnEvidence(calls=[("weather_tool", {"date": "2026-10-31", "decision": "NO SEGURO / PROHIBIDO",
            "cloud_cover_pct": 88, "reasons": ["Cobertura de nubes superior a 75%."]})])
        answer = evidence.answer()
        self.assertIn("88 %", answer)
        self.assertIn("NO SEGURO / PROHIBIDO", answer)
        self.assertNotIn("Temperatura", answer)

    async def test_mcp_structured_results_preserve_weather_and_scheduling(self):
        evidence = TurnEvidence()
        hooks = EvidenceHooks()
        for name, payload in [
            ("weather_tool", {"date": "2026-10-31", "decision": "IDEAL", "wind_speed_kmh": 7.4}),
            ("schedule_tool", {"scheduled": True, "date": "2026-10-31"}),
        ]:
            await hooks.on_tool_end(SimpleNamespace(context=evidence), None,
                SimpleNamespace(name=name), json.dumps({"result": payload}))
        answer = evidence.answer()
        self.assertIn("Decisión: IDEAL", answer)
        self.assertIn("7.4 km/h", answer)
        self.assertIn("tentativamente para 2026-10-31", answer)

    async def test_mcp_structured_error_preserves_date_rejection(self):
        evidence = TurnEvidence()
        await EvidenceHooks().on_tool_end(SimpleNamespace(context=evidence), None,
            SimpleNamespace(name="weather_tool"), json.dumps({"result": {"error": "Solo se permiten los próximos 16 días."}}))
        self.assertIn("16 días", evidence.answer())

    async def test_mcp_text_blocks_preserve_weather_and_errors(self):
        for payload in [{"date": "2026-10-31", "decision": "IDEAL"},
                        {"error": "Solo se permiten los próximos 16 días."}]:
            for output in [{"type": "text", "text": json.dumps(payload)},
                           [{"type": "text", "text": json.dumps(payload)}]]:
                evidence = TurnEvidence()
                await EvidenceHooks().on_tool_end(SimpleNamespace(context=evidence), None,
                    SimpleNamespace(name="weather_tool"), output)
                expected = "16 días" if "error" in payload else "Decisión: IDEAL"
                self.assertIn(expected, evidence.answer())
