from supabase import create_client, Client
from app.config import settings

# Initialize Supabase client
supabase_client: Client = create_client(
    supabase_url=settings.supabase_url,
    supabase_key=settings.supabase_key
)
