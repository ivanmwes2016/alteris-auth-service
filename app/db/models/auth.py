from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.db.schemas.team import normalize_email as _normalize_email
from app.helpers import password as password_rules

# Bounds the work done on a request; the password rules set the real limit.
MAX_PASSWORD_INPUT = 1024


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
    # NFKC-normalized, then checked against the password rules (app/helpers/password.py).
    # The breach check needs the network, so account.register does that one.
    password: str = Field(max_length=MAX_PASSWORD_INPUT)
    # Where the confirmation link should land once confirmed, e.g. the invite page.
    next: str | None = Field(default=None, max_length=512)
    # Cloudflare Turnstile token from the form (app/helpers/captcha.py).
    captcha_token: str | None = Field(default=None, max_length=2048)

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

    @field_validator("password")
    @classmethod
    def _password(cls, value: str) -> str:
        return password_rules.normalize(value)

    @model_validator(mode="after")
    def _password_rules(self) -> Self:
        message = password_rules.problem(self.password, self.email)
        if message is not None:
            raise ValueError(message)
        return self


class PasswordChangeRequest(BaseModel):
    """A new password for the signed-in user; checked in the route, which knows their email."""

    model_config = ConfigDict(extra="forbid")

    password: str = Field(min_length=1, max_length=MAX_PASSWORD_INPUT)


class EmailRequest(BaseModel):
    """Resend verification / forgot password: just the address (and where to return)."""

    model_config = ConfigDict(extra="forbid")

    email: str = Field(min_length=3, max_length=320)
    next: str | None = Field(default=None, max_length=512)
    captcha_token: str | None = Field(default=None, max_length=2048)

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
