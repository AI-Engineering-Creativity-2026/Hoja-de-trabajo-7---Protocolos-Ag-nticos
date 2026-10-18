"""Verifica tres procesos por HTTP con pgvector, Open-Meteo y el proveedor LLM.

Las citas se aíslan en un directorio temporal. No modifica .env ni citas del usuario.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

from dotenv import dotenv_values
import httpx

ROOT = Path(__file__).resolve().parents[1]


def wait_ready(url: str, process: subprocess.Popen, method: str = "GET"):
    deadline = time.monotonic() + 45
    with httpx.Client(timeout=2, trust_env=False) as client:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"El servidor terminó con código {process.returncode}")
            try:
                with client.stream(method, url) as response:
                    if response.status_code < 500:
                        return
            except httpx.HTTPError:
                pass
            time.sleep(0.1)
    raise TimeoutError(f"El servidor no estuvo listo: {url}")


def probe_mcp(url: str):
    with httpx.Client(timeout=120, trust_env=False, headers={"Accept": "application/json, text/event-stream"}) as http:
        def rpc(method, params):
            response = http.post(url, json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params})
            response.raise_for_status()
            data = response.json()
            assert "error" not in data, data
            return data["result"]
        result = rpc("initialize", {"protocolVersion": "2025-11-25", "capabilities": {},
                                   "clientInfo": {"name": "hdt7-network-check", "version": "1.0"}})
        http.headers["MCP-Protocol-Version"] = result["protocolVersion"]
        tools = rpc("tools/list", {})["tools"]
        assert {tool["name"] for tool in tools} == {"search_knowledge_base", "weather_tool", "schedule_tool"}
        assert rpc("resources/read", {"uri": "parachute://safety-policy"})["contents"]
        assert rpc("prompts/get", {"name": "plan_visit", "arguments": {"date": dt.date.today().isoformat()}})["messages"]
        for name, arguments in [
            ("search_knowledge_base", {"query": "¿Dónde se ubica la zona de salto?"}),
            ("weather_tool", {"date": (dt.date.today() + dt.timedelta(days=1)).isoformat()}),
        ]:
            result = rpc("tools/call", {"name": name, "arguments": arguments})
            assert not result.get("isError"), result
            assert "error" not in result.get("structuredContent", {}), result
            if name == "search_knowledge_base":
                assert result.get("content"), "La búsqueda RAG no devolvió FAQs"
            else:
                assert "decision" in json.dumps(result), result
            print(f"PASS MCP real: {name}", flush=True)
        print("PASS MCP HTTP: initialize, tools/list, resources/read, prompts/get", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    parser.add_argument("--postgres-port", default=None)
    parser.add_argument("--postgres-db", default=None)
    parser.add_argument("--load-corpus", action="store_true")
    args = parser.parse_args()
    env = dict(os.environ)
    if args.env_file:
        env.update({key: value for key, value in dotenv_values(args.env_file).items() if value is not None})
    if args.postgres_port:
        env["POSTGRES_PORT"] = args.postgres_port
    if args.postgres_db:
        env["POSTGRES_DB"] = args.postgres_db
    env.update(MCP_URL="http://127.0.0.1:18001/mcp", A2A_PUBLIC_URL="http://127.0.0.1:18000/")
    if not (env.get("GROQ_API_KEY") or env.get("OPENAI_API_KEY")):
        parser.error("Se necesita GROQ_API_KEY u OPENAI_API_KEY")
    if args.load_corpus:
        subprocess.run([sys.executable, "hdt4/src/load_corpus.py", "--corpus", "data/Corpus_FAQs_Parachute_SA_2026.txt"],
                       cwd=ROOT, env=env, check=True, timeout=300)
    with tempfile.TemporaryDirectory(prefix="hdt7-network-") as directory:
        folder = Path(directory)
        env["APPOINTMENTS_PATH"] = str(folder / "citas.json")
        processes = []
        streams = []
        try:
            for name, module, port in [("mcp", "protocols.mcp_server", "18001"),
                                        ("a2a", "protocols.a2a_server", "18000")]:
                stream = (folder / f"{name}.log").open("w+")
                streams.append(stream)
                command = [sys.executable, "-m", module, "--port", port]
                process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=stream, stderr=subprocess.STDOUT)
                processes.append(process)
                wait_ready(f"http://127.0.0.1:{port}" + ("/mcp" if name == "mcp" else "/.well-known/agent-card.json"), process)
                print(f"PASS servidor {name.upper()}: PID={process.pid}, puerto={port}", flush=True)
                if name == "mcp":
                    probe_mcp(env["MCP_URL"])
            today = dt.date.today()
            queries = ["¿Dónde se ubica la zona de salto?", "¿Quién ganó el mundial de fútbol?",
                       f"Quiero agendar una cita para {today + dt.timedelta(days=1)}"]
            queries.extend([f"Consulta el clima para {today + dt.timedelta(days=16)}", "Bye"])
            cli = subprocess.run([sys.executable, "-m", "protocols.client", "--url", "http://127.0.0.1:18000"],
                                 cwd=ROOT, env=env, input="\n".join(queries) + "\n", text=True,
                                 stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=480)
            print(cli.stdout, flush=True)
            assert cli.returncode == 0, cli.returncode
            assert "No se pudo completar la solicitud" not in cli.stdout, "La aplicación reportó un error"
            assert "Sesión finalizada" in cli.stdout
            assert "16 días" in cli.stdout or "16 dias" in cli.stdout
            streams[0].flush()
            mcp_logs = (folder / "mcp.log").read_text()
            for tool in ["search_knowledge_base", "weather_tool"]:
                assert f"tool.start name={tool}" in mcp_logs
            print("PASS cliente de terminal → A2A → MAS → MCP por sockets HTTP reales", flush=True)
        finally:
            for process in reversed(processes):
                process.terminate()
                try:
                    process.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
            for name, stream in zip(["MCP", "A2A"], streams):
                stream.close()
                print(f"\n--- Logs {name} ---", flush=True)
                for line in (folder / f"{name.lower()}.log").read_text().splitlines():
                    if "parachute." in line or "ERROR" in line:
                        print(line, flush=True)


if __name__ == "__main__":
    main()
