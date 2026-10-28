# OSCO Chatbot

For any question about OSCO / orderff data (orders, statuses, counts, stores, batch jobs, etc.), always use the
`mongodb-domain-mcp` MCP tools. Do not query MongoDB through Bash, scripts, or any other MCP server.

Typical tool order:
1. `resolve_query_target` or `search_schema_context` to pick the database, collection, and fields.
2. `get_collection_context` if field rules are unclear.
3. `count_documents`, `find_documents`, or `aggregate_documents` to answer.
4. Mention the number of tokens used. 

The data API must be running (`python3 -m backend.data_api.main`) for query tools to work.
