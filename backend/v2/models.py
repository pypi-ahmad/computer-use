"""Validated, transport-aware Computer Use model catalog."""
from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field


class ModelRoute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str
    provider: str
    transport: str
    model_id: str
    tool_version: str | None = None
    beta: str | None = None


class ComputerUseModel(BaseModel):
    model_config = ConfigDict(extra="forbid")
    logical_id: str
    display_name: str
    family: str
    supports_computer_use: bool
    context_window: int = Field(gt=0)
    max_output_tokens: int = Field(gt=0)
    coordinate_space: str
    lifecycle: str = "ACTIVE"
    supports_prompt_caching: bool = False
    max_image_long_edge: int | None = Field(default=None, gt=0)
    reasoning_efforts: list[str] = Field(default_factory=list)
    routes: list[ModelRoute] = Field(min_length=1)


class _CatalogDocument(BaseModel):
    model_config = ConfigDict(extra="ignore")
    version: str
    verified_at: str
    models: list[ComputerUseModel]


class ModelCatalog:
    def __init__(self, document: _CatalogDocument) -> None:
        invalid = [model.logical_id for model in document.models if not model.supports_computer_use]
        if invalid:
            raise ValueError(f"Non-Computer Use models in catalog: {invalid}")
        self.version = document.version
        self.verified_at = document.verified_at
        self._models = {model.logical_id: model for model in document.models}

    @classmethod
    def load(cls, path: Path | None = None) -> ModelCatalog:
        source = path or Path(__file__).resolve().parents[1] / "models" / "computer_use_models.v2.json"
        document = _CatalogDocument.model_validate_json(source.read_text(encoding="utf-8"))
        return cls(document)

    def models(self) -> list[ComputerUseModel]:
        return list(self._models.values())

    def get(self, logical_id: str) -> ComputerUseModel:
        try:
            return self._models[logical_id]
        except KeyError as exc:
            raise ValueError(f"Unknown Computer Use model: {logical_id}") from exc


CATALOG = ModelCatalog.load()
