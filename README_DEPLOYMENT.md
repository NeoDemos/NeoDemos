# NeoDemos - Deployment & Multi-City Integration Ready

**Status**: ✅ Production Ready | **Date**: March 1, 2026 | **Version**: 1.0

---

## 🚀 Quick Links

### Getting Started
- **First time?** → Start with [`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md) (15-30 min)
- **Deploy to production?** → Read [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md) (1-2 hours)
- **Add a new city?** → Follow [`docs/CITY_INTEGRATION_GUIDE.md`](docs/CITY_INTEGRATION_GUIDE.md) (30-60 min)

### Completion Report
- **What was done?** → [`docs/DEPLOYMENT_COMPLETE.md`](docs/DEPLOYMENT_COMPLETE.md)

---

## 📋 What's New

### 📚 Documentation (4 Guides, 70+ KB)

| File | Size | Purpose |
|------|------|---------|
| [`docs/CITY_INTEGRATION_GUIDE.md`](docs/CITY_INTEGRATION_GUIDE.md) | 17 KB | How to add new Dutch cities dynamically |
| [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md) | 17 KB | Production deployment guide (all platforms) |
| [`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md) | 18 KB | Local setup & configuration guide |
| [`docs/DEPLOYMENT_COMPLETE.md`](docs/DEPLOYMENT_COMPLETE.md) | 19 KB | Complete summary of this session's work |

### 🐳 Docker & Deployment (4 Files, 10 KB)

| File | Size | Purpose |
|------|------|---------|
| [`Dockerfile`](Dockerfile) | 1.2 KB | Container image (Python 3.13 slim, production-optimized) |
| [`docker-compose.yml`](docker-compose.yml) | 2.2 KB | Complete stack (PostgreSQL, FastAPI, Nginx) |
| [`nginx.conf`](nginx.conf) | 4.1 KB | Reverse proxy with SSL/HTTPS support |
| [`.env.example`](.env.example) | 2.8 KB | Environment variable template |

### 💻 Code Updates

| File | Changes | Reason |
|------|---------|--------|
| `main.py` | Lifespan context manager (lines 1-72) | Remove FastAPI deprecation warnings |

---

## 🎯 What You Can Do Now

### ✅ Setup Locally (5 minutes)
```bash
cp .env.example .env
# Edit .env: Add GEMINI_API_KEY
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/init_db.py
python scripts/ingest_data.py
python main.py
# → http://localhost:8000
```

### ✅ Deploy with Docker (30 minutes)
```bash
cp .env.example .env
# Edit .env: Add credentials
docker-compose up -d
# → http://localhost:8000
```

### ✅ Deploy to Production (1 hour)
Follow [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md)
- Linux server setup
- SSL/HTTPS configuration
- PostgreSQL installation
- Monitoring & backups
- Scaling strategies

### ✅ Add Amsterdam (30 minutes)
```bash
# Step 1: Check config is there (already pre-configured)
# Step 2: Ingest data
NEODEMOS_CITY=amsterdam python scripts/ingest_data.py
# Step 3: Extract party positions
NEODEMOS_CITY=amsterdam python scripts/extract_party_positions.py
# Step 4: Deploy
NEODEMOS_CITY=amsterdam python main.py
# → Amsterdam data now available
```

### ✅ Add Any Dutch City (30-60 minutes)
Follow [`docs/CITY_INTEGRATION_GUIDE.md`](docs/CITY_INTEGRATION_GUIDE.md):
1. Add city to `CityConfig` in `scripts/fix_notulen_data.py`
2. Run ingestion script
3. Deploy with environment variable

---

## 📁 File Organization

### Documentation
```
docs/
├── CITY_INTEGRATION_GUIDE.md     ← How to add cities dynamically
├── DEPLOYMENT_GUIDE.md            ← Production deployment (all platforms)
├── SETUP_GUIDE.md                 ← Local setup & configuration
├── DEPLOYMENT_COMPLETE.md         ← Summary of this session's work
├── EXECUTION_COMPLETE.md          ← Previous session completion (historical)
├── architecture/                  ← Design decisions & architecture
├── phases/                        ← Phase completion reports (historical)
├── investigations/                ← Technical research (RIS/ORI APIs)
└── archive/                       ← Superseded documentation
```

### Deployment Files
```
├── Dockerfile                    ← Container definition
├── docker-compose.yml            ← Complete stack
├── nginx.conf                    ← Reverse proxy
├── .env.example                  ← Environment template
└── README_DEPLOYMENT.md          ← This file
```

### Application
```
├── main.py                       ← FastAPI app (UPDATED: lifespan)
├── requirements.txt              ← Python dependencies
├── services/                     ← Business logic services
├── scripts/                      ← Data ingestion & utilities
├── templates/                    ← HTML templates
└── static/                       ← CSS, JavaScript, assets
```

### Data
```
data/
├── pipeline/                     ← Analysis outputs
├── profiles/                     ← Party position profiles
│   ├── party_profile_groenlinks_pvda.json
│   └── (will add city-specific: party_profile_groenlinks_pvda_amsterdam.json, etc.)
└── legacy/                       ← Old data (reference only)
```

---

## 🌍 Multi-City Architecture

### How Cities Work

**CityConfig** (single source of truth):
```python
# In: scripts/fix_notulen_data.py (lines 34-97)
CITIES = {
    'rotterdam': {...},
    'amsterdam': {...},
    'den_haag': {...},
    # Add more cities here
}
```

**Database** (city-isolated data):
```sql
-- All data tagged by city
SELECT * FROM meetings WHERE city = 'rotterdam'
SELECT * FROM meetings WHERE city = 'amsterdam'
-- No data cross-contamination
```

**Deployment** (environment-driven):
```bash
NEODEMOS_CITY=rotterdam python main.py
NEODEMOS_CITY=amsterdam python main.py  # Different server/container
# Same code, different data
```

### Currently Configured Cities
- ✅ **Rotterdam** - 7 notulen, 1000+ GL-PvdA mentions (data ingested)
- 🟡 **Amsterdam** - Pre-configured, ready to ingest
- 🟡 **Den Haag** - Pre-configured, ready to ingest
- 🟡 **Any other Dutch city** - Follow integration guide

---

## 🔧 Key Files & Concepts

### City Configuration Hub
**File**: `scripts/fix_notulen_data.py` (lines 34-97)  
**What**: All city metadata (keywords, mayors, committees, ORI indices)  
**Why**: Single source of truth - all scripts read from here  
**Edit**: Add new cities by extending `CITIES` dictionary

### Database Schema
**File**: `services/storage.py`  
**What**: PostgreSQL tables with city isolation  
**Why**: `meetings.city` field separates city data  
**Result**: Same database can serve 20+ cities

### Party Lens Analysis
**File**: `services/policy_lens_evaluation_service.py`  
**What**: Evaluates policies through party ideology  
**Why**: "Through the lens of the party" - NeoDemos core concept  
**City-aware**: Loads city-specific party profiles automatically

### Web API
**File**: `main.py`  
**What**: FastAPI endpoints (REST API)  
**Key**: `/api/analyse/party-lens/{agenda_item_id}`  
**City-aware**: Uses `NEODEMOS_CITY` env var for context

---

## 📊 Statistics

### Documentation Created
- **3 comprehensive guides**: 52 KB
- **Complete example**: Amsterdam integration (30 min)
- **Troubleshooting**: 12+ common issues covered
- **Checklists**: Setup, deployment, city addition

### Deployment Options
- **Local development**: macOS, Linux, Windows WSL2
- **Docker**: Single container, multi-container, docker-compose
- **Production**: Linux servers, AWS, DigitalOcean, Hetzner
- **Scaling**: Vertical, horizontal, Kubernetes

### Cities Pre-Configured
- **Rotterdam**: Fully integrated (data ingested)
- **Amsterdam**: Configured (awaiting ingestion)
- **Den Haag**: Configured (awaiting ingestion)
- **Extensible**: 20+ Dutch cities via OpenRaadsinformatie API

### Tested & Verified
- ✅ All E2E tests passing (100%)
- ✅ FastAPI app starts without errors
- ✅ Party lens evaluation working
- ✅ Database connectivity verified
- ✅ Docker stack deployable

---

## 🚀 Next Steps

### Immediate (Today)
1. Read [`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md)
2. Copy `.env.example` → `.env` and fill in variables
3. Run local setup (15 minutes)
4. Verify app works: `http://localhost:8000`

### This Week
1. Review [`docs/CITY_INTEGRATION_GUIDE.md`](docs/CITY_INTEGRATION_GUIDE.md)
2. Add Amsterdam: `NEODEMOS_CITY=amsterdam python scripts/ingest_data.py`
3. Test multi-city data isolation

### This Month
1. Follow [`docs/DEPLOYMENT_GUIDE.md`](docs/DEPLOYMENT_GUIDE.md)
2. Deploy to production server
3. Configure SSL/HTTPS
4. Setup monitoring & backups

### Ongoing
1. Add more Dutch cities (ORI API covers 20+)
2. Expand party coverage
3. Add caching layer (Redis)
4. Build analytics dashboard

---

## 🎓 Learning Path

### For Developers
1. Read: `docs/SETUP_GUIDE.md` (understand setup)
2. Read: `docs/CITY_INTEGRATION_GUIDE.md` (understand architecture)
3. Explore: `services/policy_lens_evaluation_service.py` (core logic)
4. Extend: Add features to analyze different policy areas

### For DevOps/Operations
1. Read: `docs/DEPLOYMENT_GUIDE.md` (understand deployment)
2. Read: `docs/CITY_INTEGRATION_GUIDE.md` (understand city isolation)
3. Configure: `docker-compose.yml` for your infrastructure
4. Monitor: Setup health checks, logging, backups

### For Product Managers
1. Read: `docs/CITY_INTEGRATION_GUIDE.md` (understand scope)
2. Understand: "Through the lens" concept
3. Plan: Which cities to add first
4. Prioritize: Which parties/policies matter most

---

## 📞 Support & Troubleshooting

### Setup Issues
→ See `docs/SETUP_GUIDE.md` → Troubleshooting section

### Deployment Issues
→ See `docs/DEPLOYMENT_GUIDE.md` → Troubleshooting section

### Multi-City Issues
→ See `docs/CITY_INTEGRATION_GUIDE.md` → Common Questions

### Code Issues
→ Check `docs/architecture/` for design decisions

---

## 📝 Files at a Glance

### 📄 Documentation (Read These First)
| Priority | File | Time | Purpose |
|----------|------|------|---------|
| 🔴 First | `docs/SETUP_GUIDE.md` | 15-30 min | Get it running |
| 🟡 Second | `docs/CITY_INTEGRATION_GUIDE.md` | 20 min | Understand architecture |
| 🟢 Reference | `docs/DEPLOYMENT_GUIDE.md` | 30 min (parts) | Deploy to production |
| 📋 Summary | `docs/DEPLOYMENT_COMPLETE.md` | 10 min | See what was done |

### 🐳 Infrastructure (Deploy These)
```
Dockerfile          → Build container
docker-compose.yml  → Run complete stack
nginx.conf          → Configure reverse proxy
.env.example        → Setup environment
```

### 💻 Code (Reference)
```
main.py             → FastAPI application
services/           → Business logic
scripts/            → Data ingestion & utilities
templates/          → Web UI
static/             → CSS, JS, assets
```

---

## ✅ Verification Checklist

Confirm everything is working:

```bash
# 1. App imports without errors
python -c "from main import app; print('✓ App ready')"

# 2. Database connects
psql -h localhost -U postgres -d neodemos -c "SELECT COUNT(*) FROM meetings;"

# 3. Tests pass
python -m pytest tests/ -v

# 4. E2E test works
python tests/test_party_lens_e2e.py

# 5. API responds
python main.py &
sleep 2
curl http://localhost:8000/
pkill -f "python main.py"
```

---

## 🎯 Success Criteria

✅ **You've succeeded when**:
1. [ ] App runs locally without errors
2. [ ] Database connects and loads Rotterdam data
3. [ ] Party lens analysis returns results
4. [ ] Can add Amsterdam in 30 minutes
5. [ ] Docker stack deploys successfully
6. [ ] Can switch cities with environment variable
7. [ ] Production deployment documented
8. [ ] Team understands multi-city architecture

---

## 📚 Reference

### Key Concepts
- **City-Agnostic Architecture**: Same code serves any city
- **Database Isolation**: Data separated by city column
- **Party Lens**: Evaluate policies through party perspective
- **LLM Scoring**: Semantic analysis of alignment

### Important Files
```
CityConfig          → scripts/fix_notulen_data.py:34-97
Database Schema     → services/storage.py
Party Analysis      → services/policy_lens_evaluation_service.py
Web API             → main.py (all endpoints)
```

---

## 🎉 Ready to Go!

The NeoDemos platform is **complete, documented, and production-ready**.

**Start here**: [`docs/SETUP_GUIDE.md`](docs/SETUP_GUIDE.md)

Good luck! 🚀

---

**Date**: March 1, 2026  
**Status**: ✅ Complete & Ready  
**Last Updated**: Production deployment & city integration documentation
