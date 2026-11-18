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


MAX_GOAL_LENGTH = 200
MAX_PERSONA_LENGTH = 200
MAX_VERIFICATION_TEXT_LENGTH = 100


class VerificationInput(BaseModel):
    """직접 입력한 목표의 성공 조건. preset `verification`과 같은 형식 (계약 2.3)."""
    model_config = ConfigDict(extra="forbid")
    type: Literal["PATH_AND_VISIBLE_TEXT"] = "PATH_AND_VISIBLE_TEXT"
    path: str = Field(max_length=200)
    text: str = Field(max_length=MAX_VERIFICATION_TEXT_LENGTH)
    exact: bool | None = None
    scope: str | None = None


class CreateOptimizationRequest(BaseModel):
    """UX Optimizer 생성 (고도화 v1.3 10.2). 후보 풀·평가 조건은 서버가 정하고 동결하므로 받지 않는다."""
    model_config = ConfigDict(extra="forbid")
    sourceRunId: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")   # 기준 진단 Run (선택)


class CreateRunRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # 파일 이름(results/comparisons/<experimentKey>.json)으로도 쓰므로 문자 제한
    experimentKey: str = Field(pattern=r"^[A-Za-z0-9_-]{1,64}$")
    variant: Literal["before", "after"]
    # 목표·사용자 유형은 preset ID 또는 직접 입력 문장 중 하나만 받는다 (main.create_run에서 검사)
    goalId: str | None = Field(default=None, min_length=1)
    goal: str | None = Field(default=None, max_length=MAX_GOAL_LENGTH)
    verification: VerificationInput | None = None   # goal을 직접 입력할 때만, 필수
    personaId: str | None = Field(default=None, min_length=1)
    persona: str | None = Field(default=None, max_length=MAX_PERSONA_LENGTH)
    maxSteps: int = Field(default=DEFAULT_MAX_STEPS, ge=MAX_STEPS_RANGE[0], le=MAX_STEPS_RANGE[1])
    startPath: str = DEFAULT_START_PATH
    viewport: Viewport | None = None
    promptVersion: str | None = None
    # 외부 사이트 시나리오 테스트 (#74). 등록된 사이트 설정(config/sites/<siteId>.json)만. 없으면 MORO MARKET 버전 비교
    siteId: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{1,64}$")
