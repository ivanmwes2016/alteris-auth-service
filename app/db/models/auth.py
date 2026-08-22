from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class TokenType(StrEnum):
    BEARER = "bearer"


class RegisterRequest(BaseModel):
    email: str
    password: str
    full_name: str


class LoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=1, max_length=1024)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        return value.strip().lower()


class RefreshRequest(BaseModel):
    user_id: str
    email: str


class ForgotPasswordRequest(BaseModel):
    email: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: TokenType = TokenType.BEARER
