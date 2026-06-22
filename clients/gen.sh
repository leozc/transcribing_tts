#!/usr/bin/env bash
# Regenerate typed clients from the live API's OpenAPI spec.
# Run from the repo root: bash clients/gen.sh
set -euo pipefail
cd "$(dirname "$0")/.."

# 1. dump the OpenAPI spec straight from the app (no server needed)
PYTHONPATH=src python -c "
import json
from fastapi.testclient import TestClient
from tts_serve.service import api
json.dump(TestClient(api.app).get('/openapi.json').json(), open('clients/openapi.json','w'), indent=2)
print('wrote clients/openapi.json')
"

# 2. Python client (typed)  ->  clients/tts_serve_client/
#    pipx install openapi-python-client   (or: uv tool install openapi-python-client)
openapi-python-client generate --path clients/openapi.json \
  --output-path clients/tts_serve_client --meta none --overwrite

# 3. TypeScript types  ->  clients/typescript/api.ts
npx -y openapi-typescript clients/openapi.json -o clients/typescript/api.ts

echo "done. examples: clients/python_example.py, clients/typescript/example.ts"
