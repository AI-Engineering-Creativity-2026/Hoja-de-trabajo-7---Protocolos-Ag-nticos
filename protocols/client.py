"""Cliente A2A de terminal: solo HTTP, sin importar el MAS ni las integraciones."""
from __future__ import annotations

import argparse
import asyncio
import os
from uuid import uuid4

import httpx


class A2AClient:
    def __init__(self, http: httpx.AsyncClient, base_url: str):
        self.http, self.base_url = http, base_url.rstrip("/")
        self.context_id = None

    async def discover(self) -> dict:
        response = await self.http.get(self.base_url + "/.well-known/agent-card.json")
        response.raise_for_status()
        card = response.json()
        if card.get("preferredTransport", "JSONRPC") != "JSONRPC":
            raise RuntimeError("El servidor no anuncia transporte JSONRPC")
        if card.get("protocolVersion") != "0.3.0":
            raise RuntimeError("Este cliente requiere A2A 0.3.0")
        self.endpoint = card["url"]
        return card

    async def send(self, text: str) -> str:
        message = {"kind": "message", "role": "user", "messageId": uuid4().hex,
                   "parts": [{"kind": "text", "text": text}]}
        if self.context_id:
            message["contextId"] = self.context_id
        request_id = uuid4().hex
        response = await self.http.post(self.endpoint, json={
            "jsonrpc": "2.0", "id": request_id, "method": "message/send",
            "params": {"message": message, "configuration": {"blocking": True,
                       "acceptedOutputModes": ["text/plain"]}},
        })
        response.raise_for_status()
        body = response.json()
        if body.get("jsonrpc") != "2.0" or body.get("id") != request_id:
            raise RuntimeError("Respuesta JSON-RPC inválida")
        if "error" in body:
            raise RuntimeError(body["error"]["message"])
        result = body["result"]
        self.context_id = result.get("contextId", self.context_id)
        if result["kind"] == "message":
            parts = result["parts"]
        else:
            if result["status"]["state"] != "completed":
                raise RuntimeError(result["status"].get("message", {}).get("parts", [{}])[0].get("text", "La tarea no terminó"))
            parts = [part for artifact in result.get("artifacts", []) for part in artifact["parts"]]
        return "\n".join(part["text"] for part in parts if part.get("kind") == "text")


async def chat(url: str, query: str | None = None):
    async with httpx.AsyncClient(timeout=200, trust_env=httpx.URL(url).host not in {"127.0.0.1", "localhost", "::1"}) as http:
        client = A2AClient(http, url)
        card = await client.discover()
        print(f"Conectado por A2A a {card['name']} | skills: " + ", ".join(s["id"] for s in card["skills"]))
        print(card["description"])
        if query:
            print(await client.send(query))
            return
        print("Escribe Bye o presiona Ctrl-C para salir.")
        while True:
            try:
                text = input("Tú: ")
            except EOFError:
                break
            text = text.strip()
            if text.lower() in {"bye", "salir", "exit", "quit"}:
                break
            if text:
                try:
                    print("Parachute: " + await client.send(text))
                except (httpx.HTTPError, RuntimeError) as exc:
                    print(f"No se pudo completar la solicitud: {exc}")
        print("Sesión finalizada.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("query", nargs="?")
    parser.add_argument("--url", default=os.getenv("A2A_URL", "http://127.0.0.1:8000"))
    args = parser.parse_args()
    try:
        asyncio.run(chat(args.url, args.query))
    except KeyboardInterrupt:
        print("\nSesión finalizada.")
    except (httpx.HTTPError, RuntimeError) as exc:
        parser.exit(1, f"Error de conexión A2A: {exc}\n")


if __name__ == "__main__":
    main()
