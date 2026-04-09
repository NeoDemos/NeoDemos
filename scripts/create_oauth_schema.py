#!/usr/bin/env python3
"""
Schema: OAuth 2.1 tables — clients, authorization codes, access tokens.

Creates the OAuth infrastructure for NeoDemos MCP authentication.

Usage:
    python scripts/create_oauth_schema.py
"""

import os
import psycopg2

DB_URL = os.getenv(
    "DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5432/neodemos",
)


def create_schema():
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS oauth_clients (
            id TEXT PRIMARY KEY,
            secret TEXT,
            name TEXT NOT NULL,
            redirect_uris TEXT[] NOT NULL,
            grant_types TEXT[] NOT NULL DEFAULT '{authorization_code}',
            response_types TEXT[] NOT NULL DEFAULT '{code}',
            scope TEXT NOT NULL DEFAULT 'mcp search',
            token_endpoint_auth_method TEXT NOT NULL DEFAULT 'client_secret_post',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS oauth_authorization_codes (
            code TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES oauth_clients(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            redirect_uri TEXT NOT NULL,
            scope TEXT NOT NULL DEFAULT 'mcp search',
            code_challenge TEXT NOT NULL,
            code_challenge_method TEXT NOT NULL DEFAULT 'S256',
            expires_at TIMESTAMP NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_oauth_codes_expires
            ON oauth_authorization_codes(expires_at);

        CREATE TABLE IF NOT EXISTS oauth_access_tokens (
            token TEXT PRIMARY KEY,
            client_id TEXT NOT NULL REFERENCES oauth_clients(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            scope TEXT NOT NULL DEFAULT 'mcp search',
            expires_at TIMESTAMP NOT NULL,
            refresh_token TEXT UNIQUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_oauth_access_tokens_user
            ON oauth_access_tokens(user_id);
        CREATE INDEX IF NOT EXISTS idx_oauth_refresh_token
            ON oauth_access_tokens(refresh_token);
    """)

    conn.commit()
    cur.close()
    conn.close()
    print("OAuth schema created successfully.")


if __name__ == "__main__":
    create_schema()
