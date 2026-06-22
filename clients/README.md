# Generated API clients

Typed clients generated from the service's OpenAPI 3.1 spec (`openapi.json`).
The API (`src/tts_serve/service`) is fully Pydantic-typed, so the spec — and these
clients — carry real types (not bare dicts).

## Regenerate
```bash
bash clients/gen.sh        # dumps openapi.json + regenerates both clients
```
Tools: `openapi-python-client` (`uv tool install openapi-python-client`) and
`openapi-typescript` (via `npx`).

## Python  (`tts_serve_client/`)
Generated package with typed models (`CreateTaskRequest`, `TaskRef`, `TaskStatus`,
`QueueStatus`, …) and one module per endpoint.
```bash
pip install httpx attrs python-dateutil
python clients/python_example.py 'https://youtu.be/3Amlu4y94Ho' --clip 0-20
```
See `python_example.py` — queue → poll typed `TaskStatus` → download artifact zip.

## TypeScript  (`typescript/api.ts`)
`api.ts` holds the generated `paths` / `operations` / `components` types. Pair with
the tiny [`openapi-fetch`](https://github.com/openapi-ts/openapi-typescript/tree/main/packages/openapi-fetch)
runtime for a compile-time-checked client:
```bash
npm i openapi-fetch
npx tsx clients/typescript/example.ts 'https://youtu.be/3Amlu4y94Ho'
```
See `typescript/example.ts`.

## Endpoints (from the spec)
`POST /v1/tasks` (JSON) · `POST /v1/tasks/upload` (multipart) · `GET /v1/tasks/{id}` ·
`GET /v1/tasks/{id}/artifact` · `GET /v1/tasks` · `GET /v1/queue` ·
`DELETE /v1/tasks/{id}` · `POST /v1/tasks/{id}/retry` · `GET /agent_info` · `GET /healthz`
