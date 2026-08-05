from __future__ import annotations

import re
from dataclasses import dataclass, field

from backend.app.domain.schema_models import CollectionSchema, DatabaseDefinition


TOKEN_RE = re.compile(r"[a-zA-Z0-9_]+")


def normalize(text: str) -> str:
    return " ".join(TOKEN_RE.findall(text.lower()))


def tokens(text: str) -> set[str]:
    return set(TOKEN_RE.findall(text.lower()))


@dataclass
class IndexedDocument:
    kind: str
    database: str
    collection: str | None = None
    field_name: str | None = None
    phrases: list[str] = field(default_factory=list)
    text: str = ""

    def score(self, query: str) -> int:
        normalized_query = normalize(query)
        query_tokens = tokens(query)
        indexed_tokens = tokens(self.text)
        score = len(query_tokens & indexed_tokens)
        seen_phrases: set[str] = set()
        for phrase in self.phrases:
            normalized_phrase = normalize(phrase)
            if not normalized_phrase or normalized_phrase in seen_phrases:
                continue
            seen_phrases.add(normalized_phrase)
            if normalized_phrase in normalized_query:
                phrase_token_count = len(tokens(phrase))
                score += 4 if phrase_token_count == 1 else 8 + phrase_token_count
        return score


class SchemaIndex:
    def __init__(self, databases: list[DatabaseDefinition], schemas: list[CollectionSchema]) -> None:
        self._documents = self._build(databases, schemas)

    def search(self, user_text: str, kind: str | None = None, limit: int = 10) -> list[dict]:
        docs = [doc for doc in self._documents if kind is None or doc.kind == kind]
        scored = [(doc.score(user_text), doc) for doc in docs]
        results = [
            {
                "score": score,
                "kind": doc.kind,
                "database": doc.database,
                "collection": doc.collection,
                "field": doc.field_name,
                "matched_phrases": list(
                    dict.fromkeys(phrase for phrase in doc.phrases if normalize(phrase) in normalize(user_text))
                ),
            }
            for score, doc in sorted(scored, key=lambda item: item[0], reverse=True)
            if score > 0
        ]
        return results[:limit]

    def _build(self, databases: list[DatabaseDefinition], schemas: list[CollectionSchema]) -> list[IndexedDocument]:
        documents: list[IndexedDocument] = []
        for database in databases:
            documents.append(
                IndexedDocument(
                    kind="database",
                    database=database.name,
                    phrases=[database.name, *database.aliases],
                    text=" ".join([database.name, database.display_name or "", database.description, *database.aliases]),
                )
            )
        for schema in schemas:
            collection_phrases = [
                schema.collection,
                schema.display_name or "",
                *schema.aliases,
                *schema.business_context.common_user_phrases,
            ]
            sample_text = " ".join(query.user_request for query in schema.sample_queries)
            documents.append(
                IndexedDocument(
                    kind="collection",
                    database=schema.database,
                    collection=schema.collection,
                    phrases=collection_phrases,
                    text=" ".join(
                        [
                            schema.database,
                            schema.collection,
                            schema.display_name or "",
                            schema.description,
                            schema.business_context.when_to_use or "",
                            schema.business_context.when_not_to_use or "",
                            sample_text,
                            *schema.aliases,
                        ]
                    ),
                )
            )
            for field_name, field_schema in schema.fields.items():
                documents.append(
                    IndexedDocument(
                        kind="field",
                        database=schema.database,
                        collection=schema.collection,
                        field_name=field_name,
                        phrases=[field_name, *field_schema.synonyms],
                        text=" ".join(
                            [
                                field_name,
                                field_schema.description,
                                *field_schema.synonyms,
                                *[str(example) for example in field_schema.examples],
                            ]
                        ),
                    )
                )
        return documents
