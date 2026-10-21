import os
from functools import lru_cache

from dotenv import load_dotenv

from ..types.settings import Settings

load_dotenv()


@lru_cache
def get_settings() -> Settings:
    return Settings(
        DATABASE_URL=os.getenv("DATABASE_URL"),
        SUPABASE_URL=os.getenv("SUPABASE_URL"),
        SUPABASE_ANON_KEY=os.getenv("SUPABASE_ANON_KEY"),
        SUPABASE_SERVICE_ROLE_KEY=os.getenv("SUPABASE_SERVICE_ROLE_KEY"),
        SUPABASE_JWT_SECRET=os.getenv("SUPABASE_JWT_SECRET"),
        SUPABASE_SECRET_KEY=os.getenv("SUPABASE_SECRET_KEY"),
        SUPABASE_LOGO_BUCKET_NAME=os.getenv("SUPABASE_LOGO_BUCKET_NAME"),
        RESEND_API_KEY=os.getenv("RESEND_API_KEY"),
        EMAIL_FROM=os.getenv("EMAIL_FROM"),
        ADMIN_NOTIFY_EMAILS=os.getenv("ADMIN_NOTIFY_EMAILS"),
        STRIPE_SECRET_KEY=os.getenv("STRIPE_SECRET_KEY"),
        STRIPE_WEBHOOK_SECRET=os.getenv("STRIPE_WEBHOOK_SECRET"),
        REDIS_URL=os.getenv("UPSTASH_REDIS_REST_URL"),
        REDIS_TOKEN=os.getenv("UPSTASH_REDIS_REST_TOKEN"),
        FRONTEND_URL=os.getenv("FRONTEND_URL"),
        TOKEN_TYPE=os.getenv("TOKEN_TYPE"),
        RATE_LIMITING_ENABLED=os.getenv("RATE_LIMITING_ENABLED", "true").strip().lower()
        not in {"0", "false", "no", "off"},
        RATE_LIMITS={
            name.removeprefix("RATE_LIMIT_").lower(): value
            for name, value in os.environ.items()
            if name.startswith("RATE_LIMIT_")
        },
        CLIENT_IP_HEADER=os.getenv("CLIENT_IP_HEADER", "cf-connecting-ip") or None,
        TRUSTED_PROXY_HOPS=int(os.getenv("TRUSTED_PROXY_HOPS", "1")),
    )
