from __future__ import annotations

from backend.app.catalog.base import SchemaCatalog
from backend.app.domain.tool_models import ResolvedTarget


class TargetResolver:
    def __init__(self, catalog: SchemaCatalog) -> None:
        self._catalog = catalog

    def resolve(self, user_text: str) -> ResolvedTarget:
        collections = self._catalog.resolve_collection_candidates(user_text)
        fields = self._catalog.find_field_candidates(user_text)
        if not collections and not fields:
            return ResolvedTarget(
                confidence="low",
                explanation="No catalog names, descriptions, aliases, synonyms, or sample queries matched the request.",
                warnings=["Inspect schema context before querying."],
            )

        top_field = fields[0] if fields else None
        top_collection = collections[0] if collections else None
        if top_field and (not top_collection or top_field["score"] >= top_collection["score"]):
            selected = top_field
        else:
            selected = top_collection
        database = selected.get("database")
        collection = selected.get("collection")

        candidate_fields = [
            item["field"]
            for item in fields
            if item.get("database") == database and item.get("collection") == collection and item.get("field")
        ][:5]
        score = max([item["score"] for item in [*collections, *fields]], default=0)
        confidence = "high" if score >= 8 else "medium" if score >= 3 else "low"
        phrase = ""
        if top_field and top_field.get("matched_phrases"):
            phrase = f" Matched phrase '{top_field['matched_phrases'][0]}' to field {top_field['field']}."
        explanation = f"Best target is {database}.{collection} based on catalog scoring.{phrase}"
        return ResolvedTarget(
            database=database,
            collection=collection,
            candidate_fields=candidate_fields,
            confidence=confidence,
            explanation=explanation,
            warnings=[] if confidence != "low" else ["Low confidence target; inspect collection context before querying."],
        )
