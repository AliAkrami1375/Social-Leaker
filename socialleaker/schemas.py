"""Pydantic request/response schemas."""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from .models import Role, StepStatus, TaskStatus


# ── Auth ────────────────────────────────────────────────────────────
class LoginRequest(BaseModel):
    username: str
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    username: str
    role: Role


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    role: Role
    is_active: bool
    created_at: datetime
    last_login: datetime | None = None


# ── Integrations ────────────────────────────────────────────────────
class IntegrationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    category: str
    provider: str
    status: str
    label: str | None
    secret_masked: str | None
    connected_at: datetime | None
    created_at: datetime


class PlatformConnectIn(BaseModel):
    provider: str = "instagram"
    username: str | None = None
    password: str | None = None
    label: str | None = None


# ── Tasks ───────────────────────────────────────────────────────────
class TaskIn(BaseModel):
    title: str | None = None
    prompt: str = Field(min_length=1)
    platform: str = "instagram"
    goal_target: int = 0        # 0 = collect everything discovered (no hard cap)
    max_iterations: int = 12


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    prompt: str
    platform: str
    status: TaskStatus
    goal_target: int
    max_iterations: int
    iterations: int
    collected_count: int
    requests_count: int
    tokens_est: int
    error_message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class StepOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int
    seq: int
    phase: str
    title: str
    detail: str | None
    status: StepStatus
    created_at: datetime


# ── Results ─────────────────────────────────────────────────────────
class ProfileOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    task_id: int | None
    platform: str
    username: str
    full_name: str | None
    biography: str | None
    followers: int
    following: int
    posts_count: int
    is_private: bool
    is_verified: bool
    is_business: bool
    category: str | None
    external_url: str | None
    profile_pic_url: str | None
    public_email: str | None
    public_phone: str | None
    engagement_rate: float | None
    collected_at: datetime
