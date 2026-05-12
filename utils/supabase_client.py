"""
Supabase client initialization for the Photographer Node.
"""

from supabase import create_client, Client


def get_supabase_client(config) -> Client:
    """
    Initialize and return a Supabase client.
    
    Args:
        config: Config instance with supabase_url and supabase_key
    
    Returns:
        Supabase Client instance
    """
    return create_client(config.supabase_url, config.supabase_key)
