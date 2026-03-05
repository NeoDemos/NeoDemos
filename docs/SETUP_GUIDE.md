# NeoDemos Setup & Environment Guide

**Version**: 1.0  
**Date**: March 1, 2026  
**Status**: Complete & Ready

---

## Table of Contents

1. [System Requirements](#system-requirements)
2. [Quick Setup (5 minutes)](#quick-setup-5-minutes)
3. [Full Setup Guide](#full-setup-guide)
4. [Environment Variables](#environment-variables)
5. [Database Setup](#database-setup)
6. [Initial Data Ingestion](#initial-data-ingestion)
7. [Verification & Testing](#verification--testing)
8. [Troubleshooting](#troubleshooting)

---

## System Requirements

### Minimum Requirements
- **OS**: macOS, Linux, or Windows (WSL2)
- **Python**: 3.10+ (tested and compatible with 3.13.3)
- **PostgreSQL**: 15+ (tested with 16.13)
- **RAM**: 2GB minimum, 4GB recommended
- **Disk Space**: 10GB minimum for data
- **Network**: Access to https://openraadsinformatie.nl API

### Recommended Setup
- **OS**: Ubuntu 22.04 LTS (for production)
- **Python**: 3.13.3
- **PostgreSQL**: 16.13
- **RAM**: 4GB+
- **Disk**: SSD with 20GB+ available
- **CPU**: 2+ cores

### API Keys Needed
1. **Google Gemini API Key** (free tier available)
   - Register: https://makersuite.google.com/app/apikey
   - Rate limit: 60 requests/minute (free tier)

2. **OpenRaadsinformatie API** (optional)
   - Public access available without key
   - Better rate limits with API key

---

## Quick Setup (5 minutes)

For the impatient developer:

```bash
# 1. Clone/enter project directory
cd NeoDemos

# 2. Create Python environment
python3.13 -m venv .venv
source .venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt

# 4. Copy and edit environment file
cp .env.example .env
# Edit .env: Add GEMINI_API_KEY and database credentials

# 5. Initialize database (requires PostgreSQL running)
python scripts/init_db.py

# 6. Ingest initial data (Rotterdam by default)
python scripts/ingest_data.py

# 7. Run server
python main.py
# → Open http://localhost:8000
```

---

## Full Setup Guide

### Step 1: Verify Python Version

```bash
python --version
# Expected: Python 3.10.0 or higher (tested up to 3.13.3)

# If not 3.10+, install from https://www.python.org/downloads/
# Or use pyenv (recommended):
# brew install pyenv
# pyenv install 3.13.3
# pyenv local 3.13.3
```

### Step 2: Clone & Navigate

```bash
# If not already in project
git clone https://github.com/yourusername/neodemos.git
cd neodemos

# Verify directory structure
ls -la
# Should show: data/, docs/, scripts/, services/, templates/, static/, main.py, requirements.txt
```

### Step 3: Create Python Virtual Environment

```bash
# Create virtual environment
python -m venv .venv

# Activate it
source .venv/bin/activate          # macOS/Linux
# OR
.venv\Scripts\activate.bat         # Windows CMD
# OR
.venv\Scripts\Activate.ps1         # Windows PowerShell
```

**Verify activation** (should show `.venv` in prompt):
```
(.venv) $ python --version
Python 3.13.3
```

### Step 4: Install Dependencies

```bash
# Upgrade pip/setuptools
pip install --upgrade pip setuptools wheel

# Install all project dependencies
pip install -r requirements.txt
# This installs: fastapi, uvicorn, psycopg2, google-generativeai, apscheduler, etc.

# Verify installation
python -c "import fastapi, psycopg2, google.generativeai; print('✓ All dependencies installed')"
```

### Step 5: PostgreSQL Setup

#### Option A: PostgreSQL with Homebrew (macOS)

```bash
# Install
brew install postgresql@16

# Start PostgreSQL service
brew services start postgresql@16

# Verify it's running
psql --version
# Expected: psql (PostgreSQL) 16.x

# Create default superuser (usually automatic)
psql -U postgres -c "SELECT version();"
```

#### Option B: PostgreSQL with apt (Linux/Ubuntu)

```bash
# Install
sudo apt update
sudo apt install postgresql postgresql-contrib postgresql-client

# Start service
sudo systemctl start postgresql
sudo systemctl enable postgresql  # Auto-start on reboot

# Verify
psql --version
# Expected: psql (PostgreSQL) 16.x

# Access PostgreSQL
sudo -u postgres psql
# Inside psql prompt, create superuser if needed
```

#### Option C: PostgreSQL with Docker (All Platforms)

```bash
# Install Docker first (see https://docs.docker.com/get-docker/)

# Run PostgreSQL container
docker run -d \
  --name neodemos-postgres \
  -e POSTGRES_DB=neodemos \
  -e POSTGRES_USER=postgres \
  -e POSTGRES_PASSWORD=postgres \
  -p 5432:5432 \
  -v postgres_data:/var/lib/postgresql/data \
  postgres:16

# Verify it's running
docker ps | grep postgres
# Should show: neodemos-postgres

# Test connection
psql -h localhost -U postgres -c "SELECT version();"
```

### Step 6: Create Environment File

```bash
# Copy example (if it exists)
cp .env.example .env

# OR create from scratch
cat > .env << 'EOF'
# City configuration
NEODEMOS_CITY=rotterdam

# Database (must match your PostgreSQL setup)
DB_HOST=localhost
DB_PORT=5432
DB_NAME=neodemos
DB_USER=postgres
DB_PASSWORD=postgres

# Required: Google Gemini API Key
# Get from: https://makersuite.google.com/app/apikey
GEMINI_API_KEY=your_api_key_here

# Optional: OpenRaadsinformatie API Key
# ORI_API_KEY=optional_key

# Server
HOST=0.0.0.0
PORT=8000
ENVIRONMENT=development
DEBUG=true
LOG_LEVEL=INFO
EOF
```

**Critical**: Add GEMINI_API_KEY!
```bash
# Get your API key:
# 1. Go to https://makersuite.google.com/app/apikey
# 2. Click "Create API Key"
# 3. Copy the key
# 4. Edit .env and paste it:
echo "GEMINI_API_KEY=your_actual_key_here" >> .env
```

### Step 7: Initialize Database

```bash
# Create database and tables
python scripts/init_db.py

# Expected output:
# ✓ Database 'neodemos' already exists or created
# ✓ Tables created successfully
# ✓ Database initialization complete

# Verify tables were created
psql -h localhost -U postgres -d neodemos -c "\dt"

# Expected output:
#            List of relations
#  Schema |       Name       | Type  |
# --------+------------------+-------+
#  public | agenda_items     | table |
#  public | documents        | table |
#  public | meetings         | table |
#  public | party_profiles   | table |
#  public | party_statements | table |
#  ... (and more tables)
```

### Step 8: Get API Key & Test Connection

```bash
# Test Gemini API works
python -c "
import os
from dotenv import load_dotenv
load_dotenv()
import google.generativeai as genai

key = os.getenv('GEMINI_API_KEY')
if not key:
    print('❌ GEMINI_API_KEY not set in .env')
    exit(1)

genai.configure(api_key=key)
models = genai.list_models()
print(f'✓ Gemini API connected')
print(f'✓ Available models: {len(list(models))}')
"
```

### Step 9: Ingest Initial Data

```bash
# Download and ingest Rotterdam data (first time takes 5-10 minutes)
python scripts/ingest_data.py

# Expected output:
# Fetching notulen from OpenRaadsinformatie...
# ✓ Downloaded 7 notulen
# Processing documents...
# ✓ Analyzing with LLM...
# ✓ Stored in database
# Ingestion complete!

# Verify data was loaded
psql -h localhost -U postgres -d neodemos -c "SELECT COUNT(*) FROM meetings WHERE city='rotterdam';"
# Expected: 7
```

### Step 10: Verify Installation

```bash
# Run test suite
python -m pytest tests/ -v

# Expected output:
# test_storage.py::test_db_connection PASSED
# test_party_lens_e2e.py::test_data_quality PASSED
# ... (all tests should pass)
# ========================= X passed in Y seconds =========================
```

### Step 11: Run Development Server

```bash
# Start the server
python main.py

# Expected output:
# INFO:apscheduler.scheduler:Adding job tentatively...
# INFO:     Started server process [1234]
# INFO:     Uvicorn running on http://0.0.0.0:8000
# INFO:     Daily refresh scheduler started (8 AM UTC)
```

### Step 12: Access the Application

Open browser and visit:
- **Homepage**: http://localhost:8000
- **API Docs**: http://localhost:8000/docs
- **Meetings Calendar**: http://localhost:8000/calendar

---

## Environment Variables

### Complete Reference

#### City Selection
```env
# Which city does this deployment serve?
# Default: rotterdam
# Options: rotterdam, amsterdam, den_haag, groningen, etc.
NEODEMOS_CITY=rotterdam
```

#### Database Configuration
```env
# PostgreSQL connection details
DB_HOST=localhost           # Hostname or IP
DB_PORT=5432               # Default PostgreSQL port
DB_NAME=neodemos           # Database name to use
DB_USER=postgres           # PostgreSQL user
DB_PASSWORD=postgres       # PostgreSQL password
DB_POOL_SIZE=10            # Connection pool size
DB_MAX_OVERFLOW=20         # Extra connections allowed
```

**Important**: Database user must have CREATE TABLE permissions.

#### API Keys
```env
# Required: Google Gemini API Key
# Get from: https://makersuite.google.com/app/apikey
# Rate limit: 60 requests/min (free tier)
GEMINI_API_KEY=your_actual_key_here

# Optional: OpenRaadsinformatie API Key
# For better rate limiting (not required for public API)
# ORI_API_KEY=optional_key
```

#### Server Configuration
```env
# Server binding
HOST=0.0.0.0           # Accept requests from anywhere
PORT=8000              # HTTP port

# Environment
ENVIRONMENT=development # development or production
DEBUG=true             # Show detailed error messages
LOG_LEVEL=INFO         # INFO, DEBUG, WARNING, ERROR
```

#### Optional Advanced Settings
```env
# Security
SECRET_KEY=generate_with_secrets.token_urlsafe(32)
ALLOWED_HOSTS=localhost,127.0.0.1,yourdomain.com

# Email notifications (optional)
ADMIN_EMAIL=admin@example.com
SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password

# Caching
CACHE_TTL=3600         # Cache TTL in seconds
REDIS_URL=redis://localhost:6379  # For caching (optional)

# Daily refresh schedule
DAILY_REFRESH_HOUR=8   # UTC time
DAILY_REFRESH_MINUTE=0

# Performance
WORKER_PROCESSES=4     # Background workers
MAX_REQUESTS=1000      # Requests before worker restart
```

### Generate Secure Secret Key

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
# Example output: D7K_-8mN...
# Add to .env: SECRET_KEY=D7K_-8mN...
```

### Verify Environment

```bash
# Check all required variables are set
python -c "
import os
from dotenv import load_dotenv

load_dotenv()

required = ['GEMINI_API_KEY', 'DB_HOST', 'DB_USER', 'DB_PASSWORD', 'DB_NAME']
missing = [v for v in required if not os.getenv(v)]

if missing:
    print(f'❌ Missing: {missing}')
else:
    print('✓ All required environment variables set')
"
```

---

## Database Setup

### Create Database Manually (If init_db.py Fails)

```bash
# Connect to PostgreSQL as superuser
psql -h localhost -U postgres

# Inside psql prompt, run:
CREATE DATABASE neodemos;
\c neodemos

# Then run schema creation (in Python):
python scripts/init_db.py
```

### Backup Before Changes

```bash
# Backup current database
pg_dump -h localhost -U postgres neodemos > neodemos_backup_$(date +%Y%m%d).sql

# Restore from backup if needed
psql -h localhost -U postgres neodemos < neodemos_backup_20260301.sql
```

### Reset Database (WARNING: Deletes All Data)

```bash
# Drop and recreate
psql -h localhost -U postgres -c "DROP DATABASE IF EXISTS neodemos;"
python scripts/init_db.py

# Or using Docker:
docker exec neodemos-postgres psql -U postgres -c "DROP DATABASE IF EXISTS neodemos;"
python scripts/init_db.py
```

---

## Initial Data Ingestion

### Ingest Rotterdam Data (Default)

```bash
# Uses NEODEMOS_CITY=rotterdam from .env
python scripts/ingest_data.py

# This does:
# 1. Fetch notulen from ORI API
# 2. Extract text content from PDFs
# 3. Analyze with Gemini LLM
# 4. Store in database
# 5. Extract party positions

# First run takes ~5-10 minutes depending on:
# - Network speed
# - API rate limits
# - Number of documents to process
```

### Ingest Different City

```bash
# Fetch and ingest Amsterdam data
NEODEMOS_CITY=amsterdam python scripts/ingest_data.py

# Or Den Haag
NEODEMOS_CITY=den_haag python scripts/ingest_data.py
```

### Resume Partial Ingestion

```bash
# If ingest fails midway, restart it
# Script is idempotent - won't re-download documents already fetched
python scripts/ingest_data.py
```

### Check Ingestion Status

```bash
# View database statistics
psql -h localhost -U postgres -d neodemos << 'SQL'
SELECT 
  (SELECT COUNT(*) FROM meetings) as meetings,
  (SELECT COUNT(*) FROM agenda_items) as agenda_items,
  (SELECT COUNT(*) FROM documents) as documents,
  (SELECT COUNT(*) FROM documents WHERE content IS NOT NULL) as documents_with_content;
SQL

# Expected for Rotterdam:
#  meetings | agenda_items | documents | documents_with_content
# ----------+--------------+-----------+------------------------
#        84 |          473 |      1403 |                    200+
```

---

## Verification & Testing

### 1. Unit Tests

```bash
# Run all tests
python -m pytest tests/ -v

# Run specific test file
python -m pytest tests/test_party_lens_e2e.py -v

# Run with coverage report
python -m pytest tests/ --cov=services --cov-report=html
# Opens: htmlcov/index.html
```

### 2. Integration Tests

```bash
# Test E2E party lens analysis
python tests/test_party_lens_e2e.py

# Expected output:
# [TEST 1/3] Verify clean Rotterdam notulen data...
# ✓ Rotterdam notulen linked: 7
# ✓ Average content length: 513,496 chars
# ...
# ✓ PASS
```

### 3. API Endpoint Tests

```bash
# With server running (python main.py in another terminal):

# Test homepage
curl http://localhost:8000/
# Returns: HTML page with meeting list

# Test API docs
curl http://localhost:8000/docs
# Returns: Swagger UI for API documentation

# Test party lens endpoint
curl "http://localhost:8000/api/analyse/party-lens/1?party=GroenLinks-PvdA"
# Returns: JSON with alignment analysis

# Test database connectivity
curl http://localhost:8000/health
# Expected: {"status": "ok", "database": "connected"}
```

### 4. Database Connectivity Test

```bash
# Direct database test
python -c "
from services.storage import StorageService
s = StorageService()
meetings = s.get_meetings(limit=1)
print(f'✓ Database connected, found {len(meetings)} meetings')
"
```

### 5. LLM Connectivity Test

```bash
# Test Gemini API
python -c "
import os
from dotenv import load_dotenv
load_dotenv()
import google.generativeai as genai

genai.configure(api_key=os.getenv('GEMINI_API_KEY'))
model = genai.GenerativeModel('gemini-2.5-flash')
response = model.generate_content('Hello, test')
print(f'✓ Gemini API working: {response.text[:50]}...')
"
```

---

## Troubleshooting

### Issue: "No module named psycopg2"

```bash
# Solution: Install Python dev headers
pip install psycopg2-binary
# OR
pip install --upgrade psycopg2

# Verify
python -c "import psycopg2; print('✓ psycopg2 installed')"
```

### Issue: "could not connect to server: Connection refused"

```bash
# PostgreSQL isn't running

# If using Homebrew:
brew services start postgresql@16

# If using system package (Linux):
sudo systemctl start postgresql

# If using Docker:
docker start neodemos-postgres

# Verify
psql -h localhost -U postgres -c "SELECT version();"
```

### Issue: "FATAL: database 'neodemos' does not exist"

```bash
# Database not created

# Solution 1: Let init_db.py create it
python scripts/init_db.py

# Solution 2: Create manually
psql -h localhost -U postgres -c "CREATE DATABASE neodemos;"

# Verify
psql -h localhost -U postgres -l | grep neodemos
```

### Issue: "GEMINI_API_KEY not set in environment"

```bash
# Missing API key

# Solution: Add to .env file
echo "GEMINI_API_KEY=your_actual_key" >> .env

# Get key from: https://makersuite.google.com/app/apikey
# Restart server after adding

# Verify
python -c "import os; from dotenv import load_dotenv; load_dotenv(); print(os.getenv('GEMINI_API_KEY') is not None)"
```

### Issue: "Connection pooling error: too many connections"

```bash
# Database connection pool exhausted

# Solution: Check .env settings
cat .env | grep DB_POOL
# Increase if needed:
DB_POOL_SIZE=20
DB_MAX_OVERFLOW=40

# Or: Restart server to clear connections
```

### Issue: "Rate limited by OpenRaadsinformatie API"

```bash
# ORI API rejecting requests (429 Too Many Requests)

# Solution: Add delay between requests
# Already implemented in services/open_raad.py
# Or: Add API key for better limits
echo "ORI_API_KEY=your_key" >> .env
```

### Issue: Server won't start - Port 8000 already in use

```bash
# Another process using port 8000

# Solution: Use different port
PORT=8001 python main.py

# Or: Kill the process using port 8000
lsof -ti:8000 | xargs kill -9
python main.py
```

### Issue: "Permission denied" for data/profiles directory

```bash
# Directory permissions issue

# Solution: Fix permissions
chmod -R 755 data/
chmod -R 755 output/
chmod -R 755 logs/

# Or: Run with sudo (not recommended)
sudo python main.py
```

### Debug Mode

Enable verbose logging to troubleshoot:

```bash
# Run with debug logging
LOG_LEVEL=DEBUG python main.py

# Or set in .env
LOG_LEVEL=DEBUG

# Check logs for detailed error messages
tail -f logs/application.log
```

---

## Next Steps

Once setup is complete:

1. **Explore the interface**: http://localhost:8000
2. **Read documentation**:
   - `docs/CITY_INTEGRATION_GUIDE.md` - How to add new cities
   - `docs/DEPLOYMENT_GUIDE.md` - Production deployment
3. **Add a new city** (see CITY_INTEGRATION_GUIDE.md):
   ```bash
   # Add Amsterdam
   NEODEMOS_CITY=amsterdam python scripts/fetch_notulen.py
   ```
4. **Customize party profiles** in `data/profiles/`
5. **Deploy to production** (see DEPLOYMENT_GUIDE.md)

---

## Getting Help

### Check Logs

```bash
# Application logs
tail -f logs/application.log

# Docker logs
docker-compose logs -f web

# PostgreSQL logs
docker-compose logs -f postgres
```

### Test Individual Components

```bash
# Test database
python scripts/test_database.py

# Test API connectivity
python scripts/test_api.py

# Test LLM
python scripts/test_llm.py
```

### Run Validation Script

```bash
python scripts/validate_data.py
# Reports: database health, data quality, API connectivity
```

---

## Production Checklist

Before deploying to production:

- [ ] All tests passing: `pytest tests/ -v`
- [ ] Database backed up: `pg_dump ...`
- [ ] API key secured (not in git)
- [ ] SSL/HTTPS configured
- [ ] Database credentials rotated (not default)
- [ ] LOG_LEVEL set to WARNING or ERROR
- [ ] DEBUG=false in .env
- [ ] ENVIRONMENT=production in .env
- [ ] Backup system configured
- [ ] Monitoring/alerting configured
- [ ] Documentation updated

---

**Last Updated**: March 1, 2026  
**Maintained By**: NeoDemos Development Team
