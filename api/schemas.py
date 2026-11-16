"""요청 계약 (계약 5.1, 5.2)."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

SHOP_PAGES = ("home", "mypage", "support", "orders", "refund")
P0_VIEWPORT = {"width": 1440, "height": 900}
MAX_STEPS_RANGE = (1, 15)
DEFAULT_MAX_STEPS = 8
DEFAULT_START_PATH = "/shop/home"


class Viewport(BaseModel):
    model_config = ConfigDict(extra="forbid")
    width: int = Field(ge=1)
    height: int = Field(ge=1)


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 파일 이름(results/comparisons/<experimentKey>.json)으로도 쓰므로 문자 제한
    experimentKey: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    variant: Literal["before", "after"]
    goalId: str = Field(min_length=1)
    personaId: str = Field(min_length=1)
    maxSteps: int = Field(default=DEFAULT_MAX_STEPS, ge=MAX_STEPS_RANGE[0], le=MAX_STEPS_RANGE[1])
    startPath: str = DEFAULT_START_PATH
    viewport: Viewport | None = None
    promptVersion: str | None = None
