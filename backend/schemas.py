"""Pydantic request/response models with length limits."""
from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field


class RegisterRequest(BaseModel):
    path: Optional[str] = Field(None, max_length=1024, description="Absolute path of a local repository directory")
    git_url: Optional[str] = Field(None, max_length=512, description="https/ssh URL to clone (needs CODEMIND_CLONE_WORKSPACE)")
    dest_name: Optional[str] = Field(None, max_length=64, description="Folder name inside the clone workspace")


class IndexRequest(BaseModel):
    full: bool = False


class QuestionRequest(BaseModel):
    question: str = Field(..., min_length=3, max_length=4000)
    use_llm: bool = True
    save_report: bool = True


class QualityRequest(BaseModel):
    focus: str = Field("", max_length=1000)
    paths: list[str] = Field(default_factory=list, max_length=100)
    use_llm: bool = True
    save_report: bool = True


class ImpactRequest(BaseModel):
    target: str = Field(..., min_length=1, max_length=512, description="Repository-relative file path or symbol name")
    change: str = Field("", max_length=2000)
    depth: Optional[int] = Field(None, ge=1, le=5)
    use_llm: bool = True
    save_report: bool = True


class TestPlanRequest(BaseModel):
    target: str = Field("", max_length=512)
    use_llm: bool = True
    save_report: bool = True


class DocumentationRequest(BaseModel):
    kind: Literal["architecture", "modules", "api", "setup", "dependencies", "configuration", "developer_guide", "full"] = "full"
    use_llm: bool = True
    save_report: bool = True


class ExportTestsRequest(BaseModel):
    target: str = Field("", max_length=512)


class WriteFileRequest(BaseModel):
    rel_path: str = Field(..., max_length=512)
    content: str = Field(..., max_length=2_000_000)
    approved: bool = False


class RunTestsRequest(BaseModel):
    approved: bool = False


class SettingUpdate(BaseModel):
    key: str = Field(..., max_length=64)
    value: Any


class ErrorBody(BaseModel):
    error: str
    detail: Optional[str] = None


class SignupRequest(BaseModel):
    email: str = Field(..., min_length=3, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)
    display_name: str = Field("", max_length=60)


class LoginRequest(BaseModel):
    email: str = Field(..., min_length=1, max_length=254)
    password: str = Field(..., min_length=1, max_length=128)


class ProfileUpdate(BaseModel):
    display_name: str = Field("", max_length=60)


class PasswordChange(BaseModel):
    current_password: str = Field(..., min_length=1, max_length=128)
    new_password: str = Field(..., min_length=1, max_length=128)


class AdminUserUpdate(BaseModel):
    role: Optional[Literal["user", "admin"]] = None
    status: Optional[Literal["active", "pending", "disabled"]] = None
