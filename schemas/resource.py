"""
schemas/resource.py
-------------------
Pydantic v2 schemas for request validation and API response serialisation.
"""

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field

# Allowed semester values as a literal type union
SemesterLiteral = Literal["Y1S1", "Y1S2", "Y2S1", "Y2S2", "Y3S1", "Y3S2"]


# ── Upload metadata (comes in alongside the file via Form fields) ─────────────
class ResourceCreate(BaseModel):
    title: str = Field(
        ...,
        min_length=2,
        max_length=512,
        examples=["Pharmaceutics I: Introduction to Dosage Forms"],
    )
    subject: str = Field(
        ...,
        min_length=1,
        max_length=256,
        examples=["Pharmaceutics"],
    )
    semester: SemesterLiteral = Field(..., examples=["Y1S1"])


# ── Full resource representation returned from the API ────────────────────────
class ResourceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    subject: str
    semester: str
    file_name: str
    file_path: str
    upload_date: datetime


# ── Response envelope for list endpoints ──────────────────────────────────────
class ResourceListResponse(BaseModel):
    total: int
    items: list[ResourceOut]


# ── Generic message response ──────────────────────────────────────────────────
class MessageResponse(BaseModel):
    message: str
    detail: Optional[str] = None
