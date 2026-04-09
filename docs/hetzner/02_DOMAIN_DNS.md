# 02 — Domain & DNS Configuration

**Domains**: `neodemos.nl` (primary) + `neodemos.eu` (redirect to .nl)
**Registrar + DNS**: TransIP — both domains registered, auto-renew 7 April 2027
**Registrant**: NeoDemos, Rotterdam (tak.dpa@gmail.com)
**Server IP**: `178.104.137.168`

---

## Status: DONE

| Domain | Registration | DNS | Renews |
|--------|-------------|-----|--------|
| `neodemos.nl` | Done | Done | 7 April 2027 |
| `neodemos.eu` | Done | Done | 7 April 2027 |

---

## Strategy: TransIP for Everything

TransIP handles both domain registration and DNS. Caddy on the Hetzner server handles TLS (Let's Encrypt auto-certificates). No middleman.

```
Browser → TransIP DNS → 178.104.137.168 → Caddy (auto-TLS) → FastAPI
```

---

## Step 1: Set DNS Records for neodemos.nl

### Option A: TransIP Control Panel (quickest)

1. Go to https://www.transip.nl/cp/domein-hosting/domeinnaam/ → `neodemos.nl`
2. Scroll to **DNS** section
3. Remove any existing A records for `@` and `www`
4. Add these records:

| Type | Name | TTL | Content |
|------|------|-----|---------|
| A | `@` | 300 | `178.104.137.168` |
| A | `www` | 300 | `178.104.137.168` |
| AAAA | `@` | 300 | `2a01:4f8:1c1c:9e14::1` |
| AAAA | `www` | 300 | `2a01:4f8:1c1c:9e14::1` |

5. Click Save

### Option B: TransIP REST API v6

First, generate an API key pair in the TransIP control panel:
1. Go to https://www.transip.nl/cp/account/api/
2. Create a new key pair (Read & Write)
3. Download the private key — you'll need it to sign requests

```bash
# Authenticate — get a token (requires signing with your private key)
# See: https://api.transip.nl/rest/docs.html#header-authentication

# Once you have a token, set DNS entries:
curl -X PUT "https://api.transip.nl/v6/domains/neodemos.nl/dns" \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "dnsEntries": [
      {"name": "@",   "expire": 300, "type": "A",    "content": "178.104.137.168"},
      {"name": "www", "expire": 300, "type": "A",    "content": "178.104.137.168"},
      {"name": "@",   "expire": 300, "type": "AAAA", "content": "2a01:4f8:1c1c:9e14::1"},
      {"name": "www", "expire": 300, "type": "AAAA", "content": "2a01:4f8:1c1c:9e14::1"}
    ]
  }'
```

---

## Step 2: Set DNS Records for neodemos.eu

Same process — add A/AAAA records pointing to the Hetzner server:

| Type | Name | TTL | Content |
|------|------|-----|---------|
| A | `@` | 300 | `178.104.137.168` |
| A | `www` | 300 | `178.104.137.168` |
| AAAA | `@` | 300 | `2a01:4f8:1c1c:9e14::1` |
| AAAA | `www` | 300 | `2a01:4f8:1c1c:9e14::1` |

```bash
curl -X PUT "https://api.transip.nl/v6/domains/neodemos.eu/dns" \
  -H "Authorization: Bearer <your-token>" \
  -H "Content-Type: application/json" \
  -d '{
    "dnsEntries": [
      {"name": "@",   "expire": 300, "type": "A",    "content": "178.104.137.168"},
      {"name": "www", "expire": 300, "type": "A",    "content": "178.104.137.168"},
      {"name": "@",   "expire": 300, "type": "AAAA", "content": "2a01:4f8:1c1c:9e14::1"},
      {"name": "www", "expire": 300, "type": "AAAA", "content": "2a01:4f8:1c1c:9e14::1"}
    ]
  }'
```

---

## Step 3: Configure .eu → .nl Redirect in Caddy

Since we're not using Cloudflare, the redirect happens in the Caddyfile on the server:

```
neodemos.nl, www.neodemos.nl {
    reverse_proxy localhost:8000

    handle /mcp/* {
        reverse_proxy localhost:8001
    }

    log {
        output file /data/caddy-access.log
        format json
    }
}

neodemos.eu, www.neodemos.eu {
    redir https://neodemos.nl{uri} permanent
}
```

Caddy auto-provisions TLS certificates for all four domains via Let's Encrypt. No manual cert management.

---

## Step 4: Verify DNS Propagation

After setting the records, wait a few minutes and check:

```bash
# Should return 178.104.137.168
dig neodemos.nl +short
dig neodemos.eu +short
dig www.neodemos.nl +short
dig www.neodemos.eu +short

# Check from Google's DNS
dig neodemos.nl +short @8.8.8.8

# After Caddy is running (see 03_KAMAL_DEPLOY.md):
curl -I https://neodemos.nl
# Should return HTTP/2 200

curl -I https://neodemos.eu
# Should return 301 → https://neodemos.nl/
```

---

## Optional: Email (MX Records)

If you need `noreply@neodemos.nl` for transactional email (auth flows), add MX records in TransIP:

| Type | Name | TTL | Priority | Content |
|------|------|-----|----------|---------|
| MX | `@` | 300 | 10 | (your mail provider's server) |
| TXT | `@` | 300 | — | `v=spf1 include:<provider> ~all` |

---

## DNS Records Summary

### neodemos.nl (primary)

| Type | Name | Content | Purpose |
|------|------|---------|---------|
| A | `@` | `178.104.137.168` | Root domain → server |
| A | `www` | `178.104.137.168` | www → server |
| AAAA | `@` | `2a01:4f8:1c1c:9e14::1` | IPv6 root |
| AAAA | `www` | `2a01:4f8:1c1c:9e14::1` | IPv6 www |

### neodemos.eu (redirects to .nl via Caddy)

| Type | Name | Content | Purpose |
|------|------|---------|---------|
| A | `@` | `178.104.137.168` | Root → server (Caddy redirects) |
| A | `www` | `178.104.137.168` | www → server (Caddy redirects) |
| AAAA | `@` | `2a01:4f8:1c1c:9e14::1` | IPv6 root |
| AAAA | `www` | `2a01:4f8:1c1c:9e14::1` | IPv6 www |

---

## Next Step

[03_KAMAL_DEPLOY.md](03_KAMAL_DEPLOY.md) — Configure Kamal and deploy the full stack
