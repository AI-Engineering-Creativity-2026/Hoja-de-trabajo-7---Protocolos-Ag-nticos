# Video para la entrega

Preparar `.env`, iniciar PostgreSQL y cargar el corpus según el README.
Abrir tres terminales y activar el entorno virtual en cada una:

1. `python -m protocols.mcp_server`
2. `python -m protocols.a2a_server`
3. `python -m protocols.client`

Grabar sin audio el arranque de ambos servidores y la interacción del cliente:

- Consultar una FAQ: `¿Dónde se ubica la zona de salto?`.
- Hacer una pregunta fuera del dominio.
- Solicitar una cita con una fecha YYYY-MM-DD dentro de los próximos 16 días.
- Consultar una fecha fuera del rango permitido.
- Salir con `Bye`.

Mantener visibles los logs MCP `tool.start/tool.end` y A2A
`request.start/request.end`, junto con las respuestas de la terminal.
La cita depende de las condiciones actuales de Open-Meteo y puede rechazarse.
Si se agenda, queda pendiente de confirmación del instructor.
