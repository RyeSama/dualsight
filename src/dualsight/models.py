"""Schemas produced by the perception (vision) model."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator


class OcrSpan(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    text: str
    lang: str | None = None
    region: str | None = None
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)


class VisualObject(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    name: str
    count: int | None = Field(default=None, ge=0)
    details: str | None = None
    region: str | None = None


class ChartObservation(BaseModel):
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    type: str | None = None
    axes: str | None = None
    series: list[str] = Field(default_factory=list)
    values: list[str] = Field(default_factory=list)
    takeaway: str | None = None


class Perception(BaseModel):
    """A literal, uncertainty-aware observation of one image.

    The vision model is asked for this shape, but the parser is deliberately
    forgiving: real models occasionally add prose, rename a field, or return a
    plain caption.  Anything malformed degrades into ``summary=raw_text``
    instead of failing the whole request.
    """

    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    summary: str = ""
    ocr: list[OcrSpan] = Field(default_factory=list)
    objects: list[VisualObject] = Field(default_factory=list)
    layout: str | None = None
    colors: list[str] = Field(default_factory=list)
    chart: ChartObservation | None = None
    uncertainties: list[str] = Field(default_factory=list)
    suggested_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _coerce_aliases(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if not data.get("summary"):
            for alias in ("description", "caption", "overview", "answer"):
                candidate = data.get(alias)
                if isinstance(candidate, str) and candidate.strip():
                    data["summary"] = candidate
                    break
        if isinstance(data.get("ocr"), str):
            data["ocr"] = [{"text": data["ocr"]}]
        if isinstance(data.get("objects"), str):
            data["objects"] = [{"name": data["objects"]}]
        if isinstance(data.get("uncertainties"), str):
            data["uncertainties"] = [data["uncertainties"]]
        if isinstance(data.get("colors"), str):
            data["colors"] = [data["colors"]]
        return data

    @property
    def ocr_text(self) -> str:
        return "\n".join(span.text for span in self.ocr if span.text.strip())

    def to_dict(self) -> dict[str, Any]:
        """A JSON-ready representation used by the API and evidence metadata."""

        return self.model_dump(mode="json")

    @property
    def is_empty(self) -> bool:
        return not any(
            [
                self.summary.strip(),
                self.ocr_text.strip(),
                self.objects,
                (self.layout or "").strip(),
                self.chart,
            ]
        )


class PerceptionMeta(BaseModel):
    model_config = ConfigDict(extra="ignore")

    image_id: str
    source: str
    width: int
    height: int
    original_width: int
    original_height: int
    input_bytes: int
    output_bytes: int
    provider: str
    model: str
    profile: str
    detail: str
    cached: bool = False
    latency_ms: int = 0
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    truncated: bool = False
    error: str | None = None
    notes: list[str] = Field(default_factory=list)

    def public(self) -> dict[str, Any]:
        """A compact, log-friendly projection (no OCR payload)."""

        data = self.model_dump()
        data.pop("notes", None)
        return data
