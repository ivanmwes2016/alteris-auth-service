from pydantic import BaseModel

class Settings(BaseModel):
    # App
    APP_NAME: str = "Alteris Multiple Tenant SAAS"
    ENV: str = "development"
    DEBUG: bool = False
    FRONTEND_URL:str

    # Database (Supabase Postgres)
    DATABASE_URL: str

    # Supabase Auth
    SUPABASE_URL: str
    SUPABASE_ANON_KEY: str
    # SUPABASE_SERVICE_ROLE_KEY: str
    SUPABASE_JWT_SECRET: str
    SUPABASE_SECRET_KEY:str
    SUPABASE_LOGO_BUCKET_NAME:str

    # Stripe
    STRIPE_SECRET_KEY: str
    STRIPE_WEBHOOK_SECRET: str

    # Redis (optional cache)
    REDIS_URL: str | None = None

    # Security
    JWT_ALGORITHM: str = "HS256"

    # Lambda tuning (optional)
    LAMBDA_TIMEOUT: int = 30
    LAMBDA_MEMORY_MB: int = 512