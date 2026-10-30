# OSCO Chatbot

For any question about OSCO / orderff data (orders, statuses, counts, stores, batch jobs, etc.), always use the
`mongodb-domain-mcp` MCP tools. Do not query MongoDB through Bash, scripts, or any other MCP server.

Typical tool order:
1. `resolve_query_target` or `search_schema_context` to pick the database, collection, and fields.
2. `get_collection_context` if field rules are unclear.
3. `count_orders` for order counts by date; otherwise `count_documents`, `find_documents`, or `aggregate_documents`.
4. Mention the number of tokens used. 

## Order retention: `osco.order` vs `osco.orderHistory`

**For any order count over a date range, use `count_orders(start, end, filter)`.** It picks the collection(s) from the
dates and the environment, counts each order once, chunks long ranges so they don't time out, and shows the split in
`parts`. `start`/`end` are UTC instants of the business-day boundaries (e.g. `"2025-10-28T05:00:00Z"`).

Routing rules (what `count_orders` does, and what to follow when using the other tools):

- **prod**: orders are moved from `osco.order` to `osco.orderHistory` in batches, so the two collections meet at a
  handoff point, not at a fixed age. On 2026-10-30, `osco.orderHistory` ended at 2026-05-03T23:59:59Z (about 6 months
  back) and went back to 2019; `osco.order` had full volume from 2026-05-04 on, plus a few leftover older documents.
  - Dates after the handoff: `osco.order`. Dates before it ("same date last year", last year's holidays,
    year-over-year): `osco.orderHistory` only; ignore leftover old documents in `osco.order`.
  - A range crossing the handoff ("last 6 months"): both, split at the handoff, then add. `count_orders` finds the
    handoff itself (newest `orderCreatedDate` in `osco.orderHistory`; response field `order_cutoff`) and falls back
    to "3 months before now" only if that lookup fails.
  - Confirmed rule: dates a year old or older ("same day last year", last year's holidays) always come from
    `osco.orderHistory`, whatever the handoff says. `count_orders` never splits earlier than 12 months before now.
  - With the other tools, find the handoff the same way: `find_documents` on `osco.orderHistory`, sort
    `orderCreatedDate` descending, limit 1.
  - `osco.orderHistory` is complete before the handoff. Never report it as empty or not loaded for those dates; an
    empty result means the filter is wrong. Fix it and re-run.
- **nonprod (e.g. QA):** only `osco.order` is queried, for any date. `osco.orderHistory` is never queried; the
  tools return 0 / no documents for it (`"skipped": true`).

The environment comes from `OSCO_ENVIRONMENT` in `.env` if set (`prod` / `nonprod`), otherwise from the `MONGODB_URI`
host (`-prod-` in the host name means prod). `health_check` and every query response report it.

Both collections can hold several versions of one order, and exactly one version per `orderNumber` has
`isActive: true`. `count_documents`, `aggregate_documents` and `count_orders` add `isActive: true` automatically for
`osco.order` and `osco.orderHistory` (the response shows `active_only_applied`), so a plain count is already one per
order. Pass `active_only=false` only when the user asks about versions themselves (e.g. how many times an order changed).
Do not group by `orderNumber` / sort by `versionNumber` just to de-duplicate - it is slower and can time out.

Date fields `orderCreatedDate`, `createdDate`, `modifiedDate`, `orderEditCutoffDate` and `orderCancelCutoffDate` are a
BSON date in some documents and an ISO string (e.g. `"2025-07-03T06:39:58.331Z"`) in others. Filter them with
`{"$date": "...Z"}` bounds as usual: the tools rewrite each date condition to match both forms (the executed `$or`
shows in the audit log). To bucket by hour or day in an aggregation, convert first with
`{"$toDate": "$orderCreatedDate"}` (not yet verified against the 6-digit-fraction strings like
`"2023-12-08T03:30:05.867000"`; check on a small window first).

`osco.orderHistory` is large: a full-day `aggregate_documents` can exceed the data API's 60 s timeout. Use
`count_orders` / `count_documents` for totals; for breakdowns (e.g. by banner), split the day into windows of a few
hours and add the results.

The data API must be running (`python3 -m backend.data_api.main`) for query tools to work. Restart it after editing
any schema YAML, because it validates queries against the same catalog.
