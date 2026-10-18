"""Contratos A2A y aislamiento de sesiones."""
import unittest
from unittest.mock import AsyncMock

import httpx

from protocols.a2a_server import create_app
from protocols.client import A2AClient


class ProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.runtime = AsyncMock()
        self.runtime.invoke.return_value = ("Respuesta oficial", [{"role": "assistant", "content": "Respuesta oficial"}])
        self.app = create_app(self.runtime)
        self.client = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://127.0.0.1:8000")
        self.addAsyncCleanup(self.client.aclose)

    async def rpc(self, method, params):
        return (await self.client.post("/", json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})).json()

    def message(self, message_id="m1", context="c1", text="Hola"):
        return {"message": {"kind": "message", "role": "user", "messageId": message_id,
                            "contextId": context, "parts": [{"kind": "text", "text": text}]}}

    async def test_discovery_and_client_context(self):
        client = A2AClient(self.client, "http://127.0.0.1:8000")
        card = await client.discover()
        self.assertEqual({s["id"] for s in card["skills"]}, {"faq", "weather", "appointments"})
        self.assertFalse(card["capabilities"]["streaming"])
        self.assertEqual(await client.send("Hola"), "Respuesta oficial")
        context = client.context_id
        await client.send("Continúa")
        self.assertEqual(context, client.context_id)
        self.assertEqual(self.runtime.invoke.call_args.args[0][0]["content"], "Respuesta oficial")

    async def test_task_lifecycle_and_get(self):
        result = (await self.rpc("message/send", self.message()))["result"]
        self.assertEqual(result["status"]["state"], "completed")
        fetched = (await self.rpc("tasks/get", {"id": result["id"], "historyLength": 0}))["result"]
        self.assertEqual(fetched["artifacts"], result["artifacts"])
        self.assertEqual(fetched["history"], [])
        canceled = await self.rpc("tasks/cancel", {"id": result["id"]})
        self.assertEqual(canceled["error"]["code"], -32002)

    async def test_retry_does_not_execute_twice(self):
        first = await self.rpc("message/send", self.message())
        second = await self.rpc("message/send", self.message())
        self.assertEqual(first, second)
        self.runtime.invoke.assert_awaited_once()
        changed = await self.rpc("message/send", self.message(text="Otra petición"))
        self.assertEqual(changed["error"]["code"], -32602)

    async def test_contexts_are_isolated(self):
        await self.rpc("message/send", self.message(context="first"))
        await self.rpc("message/send", self.message(context="second"))
        self.assertEqual(self.runtime.invoke.call_args.args[0], [])

    async def test_invalid_messages_never_reach_mas(self):
        for params in [{}, {"message": {}}, self.message(text=" "),
                       {**self.message(), "configuration": []}]:
            with self.subTest(params=params):
                result = await self.rpc("message/send", params)
                self.assertEqual(result["error"]["code"], -32602)
        self.runtime.invoke.assert_not_awaited()

    async def test_nontext_rejected(self):
        params = self.message()
        params["message"]["parts"] = [{"kind": "file", "file": {"uri": "https://example.com"}}]
        self.assertEqual((await self.rpc("message/send", params))["error"]["code"], -32602)
        params = {**self.message(), "configuration": {"acceptedOutputModes": ["image/png"]}}
        self.assertEqual((await self.rpc("message/send", params))["error"]["code"], -32005)

    async def test_jsonrpc_errors(self):
        bad_json = (await self.client.post("/", content="{")).json()
        self.assertEqual(bad_json["error"]["code"], -32700)
        bad_request = (await self.client.post("/", json=[])).json()
        self.assertEqual(bad_request["error"]["code"], -32600)
        self.assertEqual((await self.rpc("unknown", {}))["error"]["code"], -32601)
        self.assertEqual((await self.rpc("tasks/get", {"id": "missing"}))["error"]["code"], -32001)

    async def test_failure_is_task_failed(self):
        self.runtime.invoke.side_effect = RuntimeError("internal secret")
        result = (await self.rpc("message/send", self.message()))["result"]
        self.assertEqual(result["status"]["state"], "failed")
        self.assertNotIn("internal secret", str(result))
        self.assertNotIn("artifacts", result)
