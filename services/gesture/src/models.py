"""내부에서 쓰는 LandmarkMessage 모양. 원본은 shared/contracts/landmark.schema.json.

입력은 레포 형식과 vision 자체 형식 두 가지라서(adapter.py가 변환),
vision 형식에 없는 필드(schema_version, image_width 등)는 여기서 optional이다.
minor 버전에서 optional 필드가 추가돼도 깨지지 않도록 모르는 필드는 무시한다.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

ID_PATTERN = r"^[A-Za-z0-9_-]+$"


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Landmark(_Base):
    id: int = Field(ge=0, le=20)
    x: float
    y: float
    z: float


class Hand(_Base):
    hand_index: int | None = Field(default=None, ge=0)
    handedness: Literal["Left", "Right"] | None = None  # 촬영 방식 때문에 판별에는 쓰지 않는다
    confidence: float = Field(ge=0, le=1)
    landmarks: list[Landmark] = Field(min_length=21, max_length=21)

    @field_validator("landmarks")
    @classmethod
    def _ordered_by_id(cls, v: list[Landmark]) -> list[Landmark]:
        # 판별 코드는 순서(인덱스)로 점을 찾는다
        if [p.id for p in v] != list(range(21)):
            raise ValueError("landmarks must be ordered by id 0..20")
        return v


class LandmarkMessage(_Base):
    schema_version: str | None = Field(default=None, pattern=r"^\d+\.\d+\.\d+$")
    message_type: Literal["landmarks"] | None = None
    trace_id: str = Field(min_length=8, max_length=64, pattern=ID_PATTERN)
    session_id: str = Field(min_length=1, max_length=64, pattern=ID_PATTERN)
    frame_id: int = Field(ge=0)
    timestamp_ms: int = Field(ge=0)
    image_width: int | None = Field(default=None, ge=1)
    image_height: int | None = Field(default=None, ge=1)
    hands: list[Hand] = Field(max_length=2)
    vision_ms: float | None = Field(default=None, ge=0)
