from supabase import create_client, Client
from app.core.config import get_settings

config = get_settings()

supabase: Client = create_client(
    config.SUPABASE_URL,
    config.SUPABASE_SECRET_KEY,
)


def get_supabase() -> Client:
    return supabase