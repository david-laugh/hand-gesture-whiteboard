"""입력(LandmarkMessage) 모양 정의. 원본은 shared/contracts/landmark.schema.json.

minor 버전에서 optional 필드가 추가돼도 깨지지 않도록 모르는 필드는 무시한다.
"""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ID_PATTERN = r"^[A-Za-z0-9_-]+$"


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Landmark(_Base):
    id: int = Field(ge=0, le=20)
    x: float
    y: float
    z: float


class Hand(_Base):
    hand_index: int = Field(ge=0)
    handedness: Literal["Left", "Right"]  # 촬영 방식 때문에 판별에는 쓰지 않는다
    confidence: float = Field(ge=0, le=1)
    landmarks: list[Landmark] = Field(min_length=21, max_length=21)


class LandmarkMessage(_Base):
    schema_version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    message_type: Literal["landmarks"]
    trace_id: str = Field(min_length=8, max_length=64, pattern=ID_PATTERN)
    session_id: str = Field(min_length=1, max_length=64, pattern=ID_PATTERN)
    frame_id: int = Field(ge=0)
    timestamp_ms: int = Field(ge=0)
    image_width: int = Field(ge=1)
    image_height: int = Field(ge=1)
    hands: list[Hand] = Field(max_length=2)
    vision_ms: float = Field(ge=0)
