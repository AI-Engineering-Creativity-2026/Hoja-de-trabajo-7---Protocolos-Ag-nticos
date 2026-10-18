# Hoja de trabajo 7 — Protocolos Agénticos

CC3116 · Parachute S.A.

Esta implementación continúa el agente desarrollado en las hojas anteriores:

- Un servidor MCP reúne las herramientas de FAQs, clima y citas mediante **Streamable HTTP**.
- Un servidor A2A expone el sistema multiagente como un agente con sus skills.
- La interfaz de terminal actúa como cliente A2A.

Los tres integrantes del equipo partimos de la misma base de código de Parachute S.A., desarrollada en las hojas anteriores. Con fines de aprendizaje, decidimos realizar por separado la implementación de esta hoja de trabajo, para que cada integrante practicara directamente la integración de MCP y A2A.

## Instalación

Desde la raíz del repositorio, con Python 3.11 o superior:

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-protocols.txt -r requirements.txt
```

Crear `.env` a partir de `.env.example` y configurar el proveedor Groq u OpenAI
y la conexión a PostgreSQL.

Iniciar PostgreSQL con pgvector y cargar el corpus de FAQs:

```bash
docker compose --env-file .env -f hdt4/docker-compose.yml up -d --wait

set -a
source .env
set +a

python hdt4/src/load_corpus.py --corpus data/Corpus_FAQs_Parachute_SA_2026.txt
```

## Ejecución

Abrir tres terminales en la raíz del repositorio y activar `.venv` en cada una.

Servidor MCP:

```bash
python -m protocols.mcp_server
```

Servidor A2A, después de iniciar MCP:

```bash
python -m protocols.a2a_server
```

Cliente de terminal:

```bash
python -m protocols.client
```

Los servidores emiten logs de solicitudes y ejecuciones de herramientas.
La terminal permite realizar varias consultas y salir con `Bye` o `Ctrl-C`.

## Video de funcionamiento

[Ver el video de la implementación](https://youtu.be/0w9NhwwIQfQ).
