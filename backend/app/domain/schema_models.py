from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator


class SortDefinition(BaseModel):
    field: str
    direction: Literal["asc", "desc"] = "asc"


class BusinessContext(BaseModel):
    primary_entity: str | None = None
    common_user_phrases: list[str] = Field(default_factory=list)
    when_to_use: str | None = None
    when_not_to_use: str | None = None


class FieldSchema(BaseModel):
    type: str
    description: str = ""
    synonyms: list[str] = Field(default_factory=list)
    searchable: bool = False
    filterable: bool = False
    sortable: bool = False
    return_by_default: bool = False
    pii: bool = False
    sensitive: bool = False
    exact_match: bool = False
    indexed: bool = False
    allow_regex: bool = False
    enum: list[Any] | None = None
    examples: list[Any] = Field(default_factory=list)

    @property
    def returnable(self) -> bool:
        return not self.pii and not self.sensitive


class SampleQuery(BaseModel):
    user_request: str
    target: dict[str, Any] = Field(default_factory=dict)


class IdentityDefinition(BaseModel):
    primary_key: str = "_id"
    business_keys: list[str] = Field(default_factory=list)


class CollectionSchema(BaseModel):
    database: str
    collection: str
    display_name: str | None = None
    description: str = ""
    business_context: BusinessContext = Field(default_factory=BusinessContext)
    aliases: list[str] = Field(default_factory=list)
    identity: IdentityDefinition = Field(default_factory=IdentityDefinition)
    default_sort: SortDefinition | None = None
    default_limit: int = 10
    fields: dict[str, FieldSchema]
    sample_queries: list[SampleQuery] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_default_sort(self) -> "CollectionSchema":
        if self.default_sort and self.default_sort.field not in self.fields:
            raise ValueError(f"default_sort field '{self.default_sort.field}' is not declared in fields")
        return self

    def get_field(self, name: str) -> FieldSchema | None:
        return self.fields.get(name)


class CollectionDefinition(BaseModel):
    name: str
    schema_file: str
    description: str = ""


class DatabaseDefinition(BaseModel):
    name: str
    display_name: str | None = None
    description: str = ""
    aliases: list[str] = Field(default_factory=list)
    collections: list[CollectionDefinition] = Field(default_factory=list)


class DatabaseCatalog(BaseModel):
    databases: list[DatabaseDefinition] = Field(default_factory=list)

