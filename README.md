# MongoDB Domain MCP Server

This project is a Python backend MCP server that exposes safe, schema-aware, read-only MongoDB tools. It uses curated YAML resources to help an AI client choose the correct database, collection, and fields before running a query.

## Install

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

Copy `.env.example` to `.env` and set `MONGODB_URI`. The URI is required at server startup and is never logged in full.

MongoDB TLS verification uses Python's `certifi` CA bundle by default. If your company MongoDB endpoint requires a private/internal CA, set `MONGODB_TLS_CA_FILE` to a PEM CA bundle. Java `.jks` truststores are not consumed directly by PyMongo; export the needed CA certificate to PEM first.

## Run

```bash
python -m backend.app.main
```

The server uses MCP stdio transport by default, so logs are written to stderr.

## MCP Client Config

```json
{
  "mcpServers": {
    "mongodb-domain-mcp": {
      "command": "python",
      "args": ["-m", "backend.app.main"],
      "env": {
        "MONGODB_URI": "mongodb+srv://username:password@cluster.mongodb.net",
        "MCP_READ_ONLY": "true"
      }
    }
  }
}
```

If you use `uv`:

```json
{
  "mcpServers": {
    "mongodb-domain-mcp": {
      "command": "uv",
      "args": ["run", "python", "-m", "backend.app.main"],
      "env": {
        "MONGODB_URI": "mongodb+srv://username:password@cluster.mongodb.net"
      }
    }
  }
}
```

## Data Access (API vs direct MongoDB)

By default the server does not connect to MongoDB. Queries are built and validated locally (schema, filters, projection policy, sanitizer), then sent to a data API, and the API's response is sanitized and returned to the LLM. The API layer is [backend/app/db/api_repository.py](backend/app/db/api_repository.py).

| Setting | Purpose |
|---|---|
| `DATA_ACCESS_MODE` | `api` (default) or `mongodb` (direct connection, needs `MONGODB_URI`) |
| `API_BASE_URL` | Data API base URL (required in `api` mode) |
| `API_TOKEN` | Optional bearer token |
| `API_USERNAME` / `API_PASSWORD` | Optional Basic auth |
| `API_TIMEOUT_SECONDS` | Request timeout, default 60 |

Contract the API must implement (bodies are MongoDB Extended JSON, the API must execute read-only):

- `POST /find` `{database, collection, filter, projection, sort, limit}` returns `{"documents": [...]}`
- `POST /count` `{database, collection, filter}` returns `{"count": n}`
- `POST /aggregate` `{database, collection, pipeline, limit}` returns `{"documents": [...]}`
- `POST /explain` `{database, collection, filter}` returns `{"explain": {...}}`
- `GET /health` returns any 2xx

### Running the data API

The data API is a FastAPI service ([backend/data_api/](backend/data_api/)) and the only process that holds MongoDB credentials. It re-validates every query against the schema catalog, is read-only (no write endpoints), and requires authentication; it refuses to start without credentials.

```bash
pip install -e ".[data-api]"
export MONGODB_URI=...                 # held by this service only
export DATA_API_USERNAME=... DATA_API_PASSWORD=...   # Basic auth, and/or DATA_API_TOKEN=... for bearer
uvicorn backend.data_api.main:create_app --factory --port 8080
```

Then point the MCP server at it with `API_BASE_URL=http://<host>:8080` (plus matching `API_USERNAME`/`API_PASSWORD` or `API_TOKEN`). Interactive docs are served at `/docs`. The data API returns documents unsanitized; PII/sensitive stripping still happens in the MCP server's response sanitizer.

## How MCP Works Here

The client should first call `search_schema_context` or `resolve_query_target` for natural-language requests. After the target is clear, call `find_documents`, `count_documents`, or `aggregate_documents`. Do not guess database or collection names when schema context is available.

`find_documents` and `aggregate_documents` strip PII/sensitive fields by default (`sanitize: true`). Pass `sanitize: false` only when you explicitly need raw documents. Query-style tools include `elapsed_ms` in their response so you can see how long the server spent executing the request.

Current curated databases are `osco` and `orderff`. Their collection schema files are starter schemas created from known collection names, so `_id` is the only queryable field until you add real field metadata.

Example flow with the current starter catalog:

1. User asks: `show osco order summary records`
2. Call `resolve_query_target` with that request.
3. Use the returned `osco.orderSummary` target.
4. Call `get_collection_context` to inspect declared fields.
5. Query by `_id`, or first add real field metadata to `backend/app/resources/schemas/osco/orderSummary.yaml`.

## Adding Context

Add databases in `backend/app/resources/databases.yaml`. Each collection entry points to one YAML schema file.

Add a new collection schema under `backend/app/resources/schemas/<database>/<collection>.yaml`. Field descriptions, synonyms, examples, and business context are indexed by the resolver, so write them as explicit structured metadata. YAML comments are fine for humans but are ignored by the application.

To make natural-language queries useful, add actual collection fields and mark only safe fields as `filterable`, `searchable`, `sortable`, and `return_by_default`. Mark any customer, payment, token, or sensitive operational data with `pii: true` or `sensitive: true`.

## Security Model

The server is read-only by default and exposes no insert, update, delete, or drop tools. It validates configured database names, collection names, filter fields, projection fields, sort fields, and aggregation stages against the resource catalog.

Blocked operators and stages include `$where`, `$function`, `$accumulator`, `$out`, and `$merge`. PII and sensitive fields are stripped by default; passing `sanitize: false` to supported query tools returns them raw.

Query limits are capped by `MCP_MAX_DOCUMENTS_PER_QUERY`, and oversized responses are truncated according to `MCP_MAX_BYTES_PER_RESPONSE`.

## Tests

```bash
pytest
```
