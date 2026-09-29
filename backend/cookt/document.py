from __future__ import annotations

import re
from collections import Counter
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

STABLE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class MeasurementVariant(StrictModel):
    display_text: str = Field(min_length=1, max_length=240)
    quantity: str | None = Field(default=None, max_length=40)
    unit: str | None = Field(default=None, max_length=40)


class Ingredient(StrictModel):
    id: str = Field(pattern=STABLE_ID.pattern)
    source_text: str = Field(min_length=1, max_length=500)
    quantity: str | None = Field(default=None, max_length=40)
    unit: str | None = Field(default=None, max_length=40)
    name: str | None = Field(default=None, max_length=240)
    note: str | None = Field(default=None, max_length=240)
    us: MeasurementVariant | None = None
    metric: MeasurementVariant | None = None

    @property
    def has_unit_toggle(self) -> bool:
        return self.us is not None and self.metric is not None


class ActionSpan(StrictModel):
    id: str = Field(pattern=STABLE_ID.pattern)
    label: str = Field(min_length=1, max_length=160)
    sequence: int = Field(ge=0, le=10_000)
    start_ingredient_id: str = Field(pattern=STABLE_ID.pattern)
    end_ingredient_id: str = Field(pattern=STABLE_ID.pattern)


class RecipeSection(StrictModel):
    id: str = Field(pattern=STABLE_ID.pattern)
    heading: str | None = Field(default=None, max_length=160)
    ingredients: list[Ingredient] = Field(min_length=1, max_length=250)
    actions: list[ActionSpan] = Field(default_factory=list, max_length=500)

    @model_validator(mode="after")
    def validate_spans(self) -> RecipeSection:
        ingredient_ids = [item.id for item in self.ingredients]
        duplicates = [key for key, count in Counter(ingredient_ids).items() if count > 1]
        if duplicates:
            raise ValueError(f"duplicate ingredient ids: {', '.join(duplicates)}")
        action_ids = [item.id for item in self.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("action ids must be unique within a section")
        indexes = {item_id: index for index, item_id in enumerate(ingredient_ids)}
        for action in self.actions:
            if action.start_ingredient_id not in indexes or action.end_ingredient_id not in indexes:
                raise ValueError(f"action {action.id} references an unknown ingredient")
            if indexes[action.start_ingredient_id] > indexes[action.end_ingredient_id]:
                raise ValueError(f"action {action.id} has a reversed ingredient range")
        return self


class RecipeDocumentV1(StrictModel):
    schema_version: Literal[1] = 1
    title: str = Field(min_length=1, max_length=240)
    yield_text: str | None = Field(default=None, max_length=160)
    prep_notes: list[str] = Field(default_factory=list, max_length=30)
    sections: list[RecipeSection] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_document_ids(self) -> RecipeDocumentV1:
        section_ids = [section.id for section in self.sections]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("section ids must be unique")
        ingredient_ids = [item.id for section in self.sections for item in section.ingredients]
        if len(ingredient_ids) != len(set(ingredient_ids)):
            raise ValueError("ingredient ids must be unique across the document")
        action_ids = [item.id for section in self.sections for item in section.actions]
        if len(action_ids) != len(set(action_ids)):
            raise ValueError("action ids must be unique across the document")
        return self


class IngredientSection(StrictModel):
    id: str = Field(pattern=STABLE_ID.pattern)
    heading: str | None = Field(default=None, max_length=160)
    ingredients: list[Ingredient] = Field(min_length=1, max_length=250)

    @model_validator(mode="after")
    def validate_ingredient_ids(self) -> IngredientSection:
        ingredient_ids = [item.id for item in self.ingredients]
        if len(ingredient_ids) != len(set(ingredient_ids)):
            raise ValueError("ingredient ids must be unique within a section")
        return self


class InstructionStep(StrictModel):
    id: str = Field(pattern=STABLE_ID.pattern)
    text: str = Field(min_length=1, max_length=4_000)


class InstructionSection(StrictModel):
    id: str = Field(pattern=STABLE_ID.pattern)
    heading: str | None = Field(default=None, max_length=160)
    steps: list[InstructionStep] = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def validate_step_ids(self) -> InstructionSection:
        step_ids = [item.id for item in self.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step ids must be unique within a section")
        return self


class RecipeDocumentV2(StrictModel):
    """The lossless, conventional recipe document used by the cooking UI."""

    schema_version: Literal[2] = 2
    title: str = Field(min_length=1, max_length=240)
    yield_text: str | None = Field(default=None, max_length=160)
    notes: list[str] = Field(default_factory=list, max_length=100)
    personal_notes: str | None = Field(default=None, max_length=10_000)
    legacy_source: bool = False
    ingredient_sections: list[IngredientSection] = Field(min_length=1, max_length=30)
    instruction_sections: list[InstructionSection] = Field(min_length=1, max_length=30)

    @model_validator(mode="after")
    def validate_document_ids(self) -> RecipeDocumentV2:
        section_ids = [
            *(section.id for section in self.ingredient_sections),
            *(section.id for section in self.instruction_sections),
        ]
        if len(section_ids) != len(set(section_ids)):
            raise ValueError("section ids must be unique across the document")
        ingredient_ids = [
            item.id for section in self.ingredient_sections for item in section.ingredients
        ]
        if len(ingredient_ids) != len(set(ingredient_ids)):
            raise ValueError("ingredient ids must be unique across the document")
        step_ids = [item.id for section in self.instruction_sections for item in section.steps]
        if len(step_ids) != len(set(step_ids)):
            raise ValueError("step ids must be unique across the document")
        return self


def legacy_document_to_v2(document: RecipeDocumentV1) -> RecipeDocumentV2:
    """Expose old table recipes without inventing directions that were never retained."""

    ingredient_sections = [
        IngredientSection(
            id=f"ingredients_{index}",
            heading=section.heading,
            ingredients=section.ingredients,
        )
        for index, section in enumerate(document.sections, 1)
    ]
    instruction_sections: list[InstructionSection] = []
    for section_index, section in enumerate(document.sections, 1):
        actions = sorted(section.actions, key=lambda item: item.sequence)
        if not actions:
            continue
        instruction_sections.append(
            InstructionSection(
                id=f"instructions_{section_index}",
                heading=section.heading,
                steps=[
                    InstructionStep(id=f"legacy_{section_index}_{index}", text=action.label)
                    for index, action in enumerate(actions, 1)
                ],
            )
        )
    if not instruction_sections:
        instruction_sections = [
            InstructionSection(
                id="instructions_1",
                heading=None,
                steps=[
                    InstructionStep(
                        id="legacy_missing_1",
                        text="Original directions were not retained for this legacy recipe.",
                    )
                ],
            )
        ]
    return RecipeDocumentV2(
        title=document.title,
        yield_text=document.yield_text,
        notes=document.prep_notes,
        legacy_source=True,
        ingredient_sections=ingredient_sections,
        instruction_sections=instruction_sections,
    )


def load_document(value: Any) -> tuple[RecipeDocumentV2, bool]:
    """Return the current document shape plus whether the stored value was legacy v1."""

    if isinstance(value, RecipeDocumentV2):
        return value, value.legacy_source
    if isinstance(value, RecipeDocumentV1):
        return legacy_document_to_v2(value), True
    version = value.get("schema_version", 1) if isinstance(value, dict) else 1
    if version == 2:
        document = RecipeDocumentV2.model_validate(value)
        return document, document.legacy_source
    return legacy_document_to_v2(RecipeDocumentV1.model_validate(value)), True


Scale = Literal["0.5", "1", "2", "3"]
UnitSystem = Literal["source", "us", "metric"]
