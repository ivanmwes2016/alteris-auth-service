from supabase import Client, create_client
from supabase.lib.client_options import SyncClientOptions

from app.core.config import get_settings

config = get_settings()

supabase: Client = create_client(
    config.SUPABASE_URL,
    config.SUPABASE_SECRET_KEY,
)


def get_supabase() -> Client:
    return supabase


def get_supabase_auth() -> Client:
    """Return an isolated client for a single authentication request."""
    return create_client(
        config.SUPABASE_URL,
        config.SUPABASE_ANON_KEY,
        options=SyncClientOptions(
            auto_refresh_token=False,
            persist_session=False,
        ),
    )
