from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.db.schemas.team import normalize_email as _normalize_email

MIN_PASSWORD_LENGTH = 8


class TokenType(StrEnum):
    BEARER = "bearer"


def _safe_next(value: str | None) -> str | None:
    """Only a path on our own site: stops the email link redirecting somewhere else."""
    if value is None:
        return None
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        raise ValueError("next must be a path on this site")
    return value


class RegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    first_name: str = Field(min_length=1, max_length=100)
    last_name: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=320)
    # Supabase hashes with bcrypt, which only reads the first 72 bytes.
    password: str = Field(min_length=MIN_PASSWORD_LENGTH, max_length=72)
    # Where the confirmation link should land once confirmed, e.g. the invite page.
    next: str | None = Field(default=None, max_length=512)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return _normalize_email(value)

    @field_validator("first_name", "last_name")
    @classmethod
    def _name(cls, value: str) -> str:
        cleaned = " ".join(value.split())
        if not cleaned:
            raise ValueError("Required")
        return cleaned

    @field_validator("next")
    @classmethod
    def _next(cls, value: str | None) -> str | None:
        return _safe_next(value)


class EmailRequest(BaseModel):
    """Resend verification / forgot password: just the address (and where to return)."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    next: str | None = Field(default=None, max_length=512)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        return _normalize_email(value)

    @field_validator("next")
    @classmethod
    def _next(cls, value: str | None) -> str | None:
        return _safe_next(value)


class AcceptedResponse(BaseModel):
    message: str


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: TokenType = TokenType.BEARER
