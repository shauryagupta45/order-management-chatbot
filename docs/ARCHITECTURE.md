# OSCO Chatbot — Architecture Overview

## What this repo is today

This repository currently implements one piece of a larger system: a **schema-aware,
read-only MongoDB MCP (Model Context Protocol) server**. It exposes a curated set of
tools (`search_schema_context`, `resolve_query_target`, `get_collection_context`,
`find_documents`, `count_documents`, `aggregate_documents`) that let an AI client
safely translate a natural-language request into a validated Mongo query, without the
model ever needing to guess database names, collection names, or field names.

Everything an MCP client needs to know about `osco`/`orderff` databases and their
collections is declared as YAML resources under `backend/app/resources/`, and every
query is checked against that catalog before it touches Mongo (allowed fields,
blocked operators/stages, document/byte limits, PII sanitization). See the root
[README.md](../README.md) for the operational details of this server.

## The bigger picture: a multi-domain chatbot

This Mongo MCP server is the first of several planned **domain MCP servers**, each
responsible for one backend system and exposing it through the same MCP contract:

| Domain | MCP server | Status |
|---|---|---|
| MongoDB (`osco`, `orderff`) | this repo | ✅ implemented |
| Kafka (topics, consumer lag, message inspection) | planned | 🔲 not started |
| Grafana (dashboards, panel queries, alert status) | planned | 🔲 not started |
| Knowledge base (runbooks, docs, FAQs — likely RAG-backed) | planned | 🔲 not started |

The intent is **one MCP server per domain**, each independently deployable and
independently schema-curated, rather than one monolithic tool server. This keeps the
blast radius of a bad tool call contained to its domain, lets each domain evolve its
own catalog/security model (e.g., Kafka ACL awareness, Grafana read scopes), and lets
new domains be added without touching existing ones.

A single chatbot application sits in front of all of these MCP servers as an
**MCP client** and is the piece that does not exist in this repo yet.

## What's still missing: the orchestration/LLM layer

None of the MCP servers described above are useful on their own — an MCP server only
exposes *tools*; something has to decide, per user turn, which tool(s) to call, with
what arguments, in what order, and how to turn the results back into a natural-language
answer. That reasoning loop requires an LLM, plus an orchestration layer around it.

### Why an LLM is required

- **Natural language → tool call.** A user asks "how many osco orders shipped late last
  week in the Denver DC" — turning that into a `resolve_query_target` call followed by
  a validated `aggregate_documents` call (correct database, collection, fields, filter
  shape) is exactly the kind of ambiguous, context-dependent translation an LLM does
  and hardcoded parsing cannot.
- **Cross-domain reasoning.** Once Kafka/Grafana/KB servers exist, a single question
  may require chaining tools across domains (e.g., "why did the order pipeline spike in
  errors this morning" → check Grafana panel → check Kafka consumer lag → look up
  related Mongo records → cross-reference a KB runbook). Deciding which domains are
  relevant and in what order is a reasoning task, not a routing table.
- **Tool selection among growing catalogs.** As more MCP servers and tools are added,
  the model needs to read each server's declared tool/resource schemas and pick the
  right one(s) — this is the core MCP value proposition, but it only pays off with a
  capable LLM driving it.
- **Grounded, safe summarization.** Raw Mongo documents, Kafka messages, and Grafana
  panel data need to be turned into an answer a human can act on, while respecting the
  PII/sanitization rules the MCP layer already enforces (e.g., not re-surfacing
  sensitive fields the tool intentionally stripped).
- **Multi-turn context.** Follow-up questions ("now filter that to just DC 4102")
  require carrying forward the resolved target/state from prior turns rather than
  re-resolving from scratch.

This means the LLM is not an optional add-on for a nicer UI — it **is** the
orchestration engine that makes the MCP servers usable at all. Any request to stand up
this application needs to include LLM access (model + API budget) as a first-class,
required dependency, not a follow-on enhancement.

### Why LangChain / LangGraph for orchestration

The orchestration layer connecting the LLM to the growing set of MCP servers should be
built on **LangChain** (for MCP client integration, tool-calling abstractions, and
prompt/message plumbing) and **LangGraph** (for the actual control flow), rather than a
hand-rolled agent loop:

- **Multi-MCP-server routing as a graph.** LangGraph models the agent as an explicit
  state graph, which maps naturally onto "route to the right MCP server(s), call tools,
  reflect, possibly call another domain's server, then respond." This is materially
  harder to keep correct as a single unstructured prompt loop once 4 MCP servers are
  live.
- **Native MCP client support.** LangChain has first-class adapters for MCP servers,
  so each new domain server (Kafka, Grafana, KB) can be registered as a tool source
  with minimal glue code, matching the "one server per domain" design already
  established here.
- **Stateful, resumable multi-turn conversations.** LangGraph's checkpointing gives
  multi-turn context (see above) and human-in-the-loop approval points essentially for
  free — relevant given this system will eventually touch operational data across
  Mongo, Kafka, and Grafana.
- **Testability and observability.** A graph of explicit nodes (classify intent →
  select domain(s) → call tool(s) → validate/sanitize → respond) is easier to unit test
  and trace than an opaque agent loop, which matters for a system whose MCP layer
  already treats validation and auditability as first-class (see the query validator
  and response sanitizer in this repo).

### Net new component required

To go from "four domain MCP servers" to "one chatbot," this project needs a new
orchestration service (not yet present in this repo) that:

1. Runs a LangGraph agent backed by an LLM.
2. Registers each domain's MCP server as a LangChain MCP tool source.
3. Owns conversation state/session management across turns.
4. Applies any additional cross-domain safety policy on top of what each MCP server
   already enforces individually.

This is the piece that should be scoped and requested alongside (or ahead of) the
remaining Kafka/Grafana/knowledge-base MCP servers, since none of those servers are
independently useful to an end user without it.
