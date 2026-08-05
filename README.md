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

## How MCP Works Here

The client should first call `search_schema_context` or `resolve_query_target` for natural-language requests. After the target is clear, call `find_documents`, `count_documents`, or `aggregate_documents`. Do not guess database or collection names when schema context is available.

`find_documents` and `aggregate_documents` return unsanitized raw documents by default for developer workflows. Pass `sanitize: true` only when you explicitly want PII/sensitive fields stripped. Query-style tools include `elapsed_ms` in their response so you can see how long the server spent executing the request.

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

Blocked operators and stages include `$where`, `$function`, `$accumulator`, `$out`, and `$merge`. PII and sensitive fields are returned by default for developer use, but can be stripped by passing `sanitize: true` to supported query tools.

Query limits are capped by `MCP_MAX_DOCUMENTS_PER_QUERY`, and oversized responses are truncated according to `MCP_MAX_BYTES_PER_RESPONSE`.

## Tests

```bash
pytest
```
