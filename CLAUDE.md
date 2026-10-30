# OSCO Chatbot

For any question about OSCO / orderff data (orders, statuses, counts, stores, batch jobs, etc.), always use the
`mongodb-domain-mcp` MCP tools. Do not query MongoDB through Bash, scripts, or any other MCP server.

Typical tool order:
1. `resolve_query_target` or `search_schema_context` to pick the database, collection, and fields.
2. `get_collection_context` if field rules are unclear.
3. `count_documents`, `find_documents`, or `aggregate_documents` to answer.
4. Mention the number of tokens used. 

## Order retention: `osco.order` vs `osco.orderHistory`

In prod, `osco.order` keeps only the last 3 months of orders; older orders are purged from it and live in
`osco.orderHistory`, which goes back to at least December 2023. Pick the collection from the requested date, not from
which collection happens to return data:

- Date within the last 3 months (today, yesterday, last week, last month): use `osco.order`, filtering
  `orderCreatedDate` as a BSON date, e.g. `{"$gte": {"$date": "2026-10-28T05:00:00Z"}, "$lt": {"$date": "2026-10-29T05:00:00Z"}}`.
- Date older than 3 months ("same date last year", last year's holidays, year-over-year): use `osco.orderHistory`
  only, with the same BSON-date filter. Do not query `osco.order` for these dates.
- A range that crosses the boundary: query both, combine the results, and count each `orderNumber` once.
- Old documents can linger in `osco.order` past the window; ignore them. A zero or tiny count from `osco.order` for an
  old date means the orders were purged, not that there were none.
- Check the environment before interpreting an empty result. `health_check` and every query tool response report
  `environment` (`prod` / `nonprod`, from `OSCO_ENVIRONMENT` or inferred from the `MONGODB_URI` host).
  - **prod:** `osco.orderHistory` is complete back to December 2023. Never treat it as empty or not loaded. If it
    returns nothing for a date in that range, the filter is wrong (wrong day boundary, or - only for late-2023
    records - a BSON date where the value is a string). Fix the filter and re-run; report zero only if the corrected
    query still matches nothing.
  - **nonprod (e.g. QA):** either collection may hold only partial data. If `osco.orderHistory` returns nothing, say the
    data is not loaded in this environment rather than reporting zero orders.

Both collections can hold several versions of one order, and exactly one version per `orderNumber` has
`isActive: true`. `count_documents` and `aggregate_documents` add `isActive: true` automatically for `osco.order` and
`osco.orderHistory` (the response shows `active_only_applied`), so a plain count is already one per order. Pass
`active_only=false` only when the user asks about versions themselves (e.g. how many times an order changed).

Notes for `osco.orderHistory`:
1. `orderCreatedDate` is a **BSON date** (UTC), same as `osco.order`. Verified in prod: `countDocuments` with
   `{"orderCreatedDate": {"$gte": ISODate("2025-10-28T07:00:00Z"), "$lt": ISODate("2025-10-29T07:00:00Z")}, "isActive": true}`
   returns ~99.5k. Only some very old records (late 2023) store it as an ISO string (e.g. `"2023-12-08T03:30:05.867000"`);
   for those dates, if the BSON-date filter returns nothing, retry with the string form.
2. Use the automatic `isActive: true` to count each order once. Do not group by `orderNumber` / sort by
   `versionNumber` just to de-duplicate - it is slower and can time out.
3. The collection is large: a full-day `aggregate_documents` can exceed the data API's 60 s timeout. Use
   `count_documents` for totals; for breakdowns (e.g. by banner), split the day into windows of a few hours and add
   the results.

The data API must be running (`python3 -m backend.data_api.main`) for query tools to work. Restart it after editing
any schema YAML, because it validates queries against the same catalog.
