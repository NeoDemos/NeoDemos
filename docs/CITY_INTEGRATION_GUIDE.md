# NeoDemos City Integration Guide

**Version**: 1.0  
**Date**: March 1, 2026  
**Status**: Complete & Production-Ready

---

## Overview

NeoDemos is designed as a **multi-city system** where adding a new Dutch city requires minimal code changes. This guide maps all the files and configurations needed to add a new city dynamically.

### Current Supported Cities
- ✅ **Rotterdam** (fully integrated, 7 notulen)
- 🟡 **Amsterdam** (configured, ready to ingest data)
- 🟡 **Den Haag** (configured, ready to ingest data)

---

## Architecture: City-Agnostic Design

The system achieves multi-city support through:

1. **CityConfig class** - Centralized city metadata (keywords, mayors, committees, ORI indices)
2. **Environment variables** - `NEODEMOS_CITY` to specify active city
3. **Parameterized database queries** - All city-specific logic uses city name as filter
4. **Modular services** - No hard-coded city names in business logic

---

## File Map: Where City Logic Lives

### 1. **Configuration Files** (Highest Priority)

#### `scripts/fix_notulen_data.py` - City Configuration Hub
**Purpose**: Defines all city metadata  
**Line 34-97**: `CityConfig` class with CITIES dictionary

```python
CITIES = {
    'rotterdam': {
        'official_name': 'Rotterdam',
        'keywords': ['rotterdam', 'stationsplein'],       # City identification keywords
        'mayors': ['Aboutaleb', 'Schouten'],             # Known mayors (period-specific)
        'committees': [                                   # Local committees
            'Gemeenteraad',
            'Commissie Mobiliteit',
            'Commissie Zorg',
            # ... add more as discovered
        ],
        'ori_index': 'ori_rotterdam_20250629013104',     # OpenRaadsinformatie API index
        'known_wrong_docs': [                             # Documents wrongly linked
            '216305', '230325', # Amsterdam docs
        ]
    },
    # Add new cities here...
}
```

**To add a new city (e.g., Utrecht)**:
1. Research OpenRaadsinformatie API to find ORI index for Utrecht
2. Find list of current/recent mayors
3. Add to CITIES dict:
```python
'utrecht': {
    'official_name': 'Utrecht',
    'keywords': ['utrecht', 'domstad'],  # Add city-specific terms
    'mayors': ['Sharon Dijksma'],        # Current mayor
    'committees': ['Gemeenteraad'],      # Will expand as data added
    'ori_index': 'ori_utrecht_20250629013104',  # Replace with real ORI index
    'known_wrong_docs': []               # Will populate after first data ingest
}
```

---

### 2. **Data Ingestion Scripts**

#### `scripts/fetch_notulen.py` - Download Meeting Minutes
**Purpose**: Fetches notulen (meeting minutes) from OpenRaadsinformatie API  
**How it uses city config**:
- Reads `NEODEMOS_CITY` env var
- Uses `CityConfig.get(city)` to load city metadata
- Queries ORI API with correct `ori_index` for that city

**To integrate new city**:
- After adding city to `CityConfig`, this script auto-detects it
- Run: `NEODEMOS_CITY=utrecht python scripts/fetch_notulen.py`
- Script will fetch all notulen for Utrecht from ORI API

#### `scripts/ingest_data.py` - Main Data Pipeline
**Purpose**: Multi-step data processing (fetch → clean → analyze → store)  
**Features**:
- Auto-detects city from `NEODEMOS_CITY` env var
- Calls `fetch_notulen.py` for that city
- Stores all data in PostgreSQL with city-aware queries

**To integrate new city**:
```bash
NEODEMOS_CITY=amsterdam python scripts/ingest_data.py
```

#### `scripts/fix_notulen_data.py` - Data Quality Repair
**Purpose**: Unlink non-target-city documents, re-fetch truncated content  
**Usage**:
```bash
python scripts/fix_notulen_data.py --city den_haag
```
- Uses `CityConfig` to identify which documents belong to which city
- Uses `known_wrong_docs` list to identify and unlink non-city documents
- Re-fetches content from ORI API

---

### 3. **Service Layer** (Business Logic)

#### `services/open_raad.py` - ORI API Client
**Where city matters**: `OpenRaadService.search()` method  
**Current behavior**: Takes `ori_index` parameter

**To integrate new city**:
- Passes city's ORI index from `CityConfig` when calling API
- No code changes needed - already parameterized

#### `services/storage.py` - PostgreSQL Layer
**Where city matters**: All queries filter by city name  
**Key tables**:
- `documents`: Stores notulen content (`document_type`, `source_document_id`, `content`)
- `meetings`: Stores meeting metadata (include city origin)
- `agenda_items`: Linked to meetings by city

**Example query** (already city-aware):
```python
def get_documents_for_city(self, city_name):
    """Get all documents for a specific city"""
    self.cur.execute("""
        SELECT d.* FROM documents d
        JOIN meetings m ON d.meeting_id = m.id
        WHERE m.city = %s
    """, (city_name,))
```

**To integrate new city**:
- Add records with `city='utah'` to `meetings` table
- All other queries automatically work for that city
- No code changes needed

#### `services/party_position_profile_service.py` - Party Profiles
**Where city matters**: Loading city-specific party positions  
**Files involved**:
- `data/profiles/party_profile_groenlinks_pvda.json` (Rotterdam-specific now)
- Will create `data/profiles/party_profile_groenlinks_pvda_amsterdam.json` for Amsterdam

**To integrate new city**:
1. Run party position extraction for new city:
```bash
NEODEMOS_CITY=groningen python scripts/extract_party_positions.py
```
2. Creates `data/profiles/party_profile_groenlinks_pvda_groningen.json`
3. Web API auto-loads correct profile based on `NEODEMOS_CITY`

#### `services/policy_lens_evaluation_service.py` - Party Lens Analysis
**Where city matters**: Returns city-specific alignment scores  
**Current behavior**: Already parameterized by city

**To integrate new city**:
- No code changes needed
- Just provide city-specific party profile file
- API returns results relative to that city's party positions

---

### 4. **Web Application** (FastAPI)

#### `main.py` - REST API Endpoints
**Key endpoints**:

**`GET /`** - Homepage  
- Lists meetings for current city (`NEODEMOS_CITY`)
- Fetches: `storage.get_meetings(city=NEODEMOS_CITY, limit=50)`

**`GET /api/analyse/party-lens/{agenda_item_id}`**  
- Evaluates agenda item through party lens
- Query param: `party` (default: GroenLinks-PvdA)
- Auto-loads party profile for current city

**To integrate new city**:
1. Set environment variable: `export NEODEMOS_CITY=rotterdam`
2. Restart FastAPI server
3. All endpoints automatically use Rotterdam data
4. No code changes

---

### 5. **Frontend Templates**

#### `templates/index.html` - Homepage
**Where city matters**: Displays meetings list for current city  
**Current**: Shows `meetings` passed from `main.py`

#### `templates/meeting.html` - Meeting Detail + Party Lens UI
**Where city matters**:
- Line 68-100: Standpuntanalyse section
- Party dropdown shows parties available for current city
- Party profile loaded based on current city

**To integrate new city**:
- No template changes needed
- Party profiles auto-load from `data/profiles/` for current city

---

### 6. **Database Schema**

#### Tables & City Integration

**`meetings` table**:
```sql
CREATE TABLE meetings (
    id SERIAL PRIMARY KEY,
    name VARCHAR(255),
    date DATE,
    city VARCHAR(100),  -- ← KEY: Stores which city
    source_id VARCHAR(100),
    ori_index VARCHAR(100),
    UNIQUE(source_id, city)
);
```

**`documents` table**:
```sql
CREATE TABLE documents (
    id SERIAL PRIMARY KEY,
    meeting_id INTEGER REFERENCES meetings(id),
    name VARCHAR(255),
    content TEXT,
    document_type VARCHAR(50),
    source_document_id VARCHAR(100),
    created_at TIMESTAMP DEFAULT NOW()
    -- Note: city is inferred via meeting_id → meetings.city
);
```

**`agenda_items` table**:
```sql
CREATE TABLE agenda_items (
    id SERIAL PRIMARY KEY,
    meeting_id INTEGER REFERENCES meetings(id),
    name VARCHAR(255),
    -- Note: city is inferred via meeting_id → meetings.city
);
```

**To integrate new city**:
- Simply insert records with `city='new_city'` in `meetings` table
- All foreign key relationships work automatically
- No schema changes needed

---

## Step-by-Step: Adding Amsterdam (Live Example)

### Step 1: Verify City Config (Already Done)
```python
# In scripts/fix_notulen_data.py, line 72-78
'amsterdam': {
    'official_name': 'Amsterdam',
    'keywords': ['amsterdam', 'gemeente amsterdam'],
    'mayors': ['Femke van den Driessche'],
    'committees': ['Gemeenteraad'],
    'ori_index': 'ori_amsterdam_20250629013104',
    'known_wrong_docs': []
}
```

### Step 2: Fetch Amsterdam Data
```bash
cd /path/to/NeoDemos
NEODEMOS_CITY=amsterdam python scripts/fetch_notulen.py
```

This will:
1. Load Amsterdam config from `CityConfig.CITIES['amsterdam']`
2. Query ORI API with `ori_index='ori_amsterdam_20250629013104'`
3. Download all Amsterdam gemeenteraad notulen
4. Store with `city='amsterdam'` in database

### Step 3: Create Amsterdam Party Profiles
```bash
NEODEMOS_CITY=amsterdam python scripts/extract_party_positions.py
```

This will:
1. Analyze Amsterdam notulen for party statements
2. Create `data/profiles/party_profile_groenlinks_pvda_amsterdam.json`
3. Extract GroenLinks-PvdA positions specific to Amsterdam

### Step 4: Switch to Amsterdam in Web App
```bash
export NEODEMOS_CITY=amsterdam
python main.py
```

Now the web app will:
- Show Amsterdam meetings on homepage
- Load Amsterdam-specific party profiles
- Return Amsterdam-based alignment scores

### Step 5: Verify Data Quality
```bash
python scripts/fix_notulen_data.py --city amsterdam
```

This will:
1. Check for any non-Amsterdam documents
2. Report data quality metrics
3. Save results to `output/test_results/notulen_data_fix_amsterdam.json`

---

## Environment Variable Configuration

### Primary Configuration: `NEODEMOS_CITY`

**Purpose**: Selects which city the system operates on  
**Default**: `rotterdam`  
**Usage**:
```bash
# Use Rotterdam (default)
python main.py

# Use Amsterdam
export NEODEMOS_CITY=amsterdam
python main.py

# Use Den Haag
NEODEMOS_CITY=den_haag python main.py
```

### Other Environment Variables (`.env` file)

```env
# City selection
NEODEMOS_CITY=rotterdam

# Database
DB_HOST=localhost
DB_PORT=5432
DB_NAME=neodemos
DB_USER=postgres
DB_PASSWORD=postgres

# API Keys
GEMINI_API_KEY=your_key_here
ORI_API_KEY=optional_key

# Server
HOST=0.0.0.0
PORT=8000
ENVIRONMENT=production
```

---

## Directory Structure for Multi-City Support

```
NeoDemos/
├── data/
│   ├── pipeline/                          # Analysis pipeline outputs (city-agnostic)
│   │   ├── groenlinks_pvda_detailed_proposals.json
│   │   └── ...
│   │
│   ├── profiles/                          # CITY-SPECIFIC party profiles
│   │   ├── party_profile_groenlinks_pvda.json
│   │   ├── party_profile_groenlinks_pvda_amsterdam.json
│   │   ├── party_profile_groenlinks_pvda_groningen.json
│   │   └── ...
│   │
│   └── legacy/
│       └── neodemos.db                    # Old SQLite (reference only)
│
├── scripts/
│   ├── fix_notulen_data.py               # ← CITY CONFIG HUB (line 34-97)
│   ├── fetch_notulen.py                  # Uses NEODEMOS_CITY
│   ├── ingest_data.py                    # Uses NEODEMOS_CITY
│   ├── extract_party_positions.py        # Uses NEODEMOS_CITY
│   └── ...
│
├── services/
│   ├── open_raad.py                      # ORI API client (parameterized)
│   ├── storage.py                        # PostgreSQL (city-aware queries)
│   ├── party_position_profile_service.py # (city-specific profiles)
│   ├── policy_lens_evaluation_service.py # (city-agnostic evaluation)
│   └── ...
│
├── main.py                               # FastAPI (reads NEODEMOS_CITY)
├── .env                                  # Sets NEODEMOS_CITY
└── ...
```

---

## Adding a New City: Complete Checklist

### ✅ Phase 1: Configuration (15 minutes)

- [ ] Research OpenRaadsinformatie for city's ORI index
- [ ] Add city to `scripts/fix_notulen_data.py` CityConfig (line 34-97)
- [ ] Include: official_name, keywords, mayors, committees, ori_index, known_wrong_docs

**Example**:
```python
'groningen': {
    'official_name': 'Groningen',
    'keywords': ['groningen', 'gemeente groningen'],
    'mayors': ['Koen Schuiling'],
    'committees': ['Gemeenteraad'],
    'ori_index': 'ori_groningen_20250629013104',
    'known_wrong_docs': []
}
```

### ✅ Phase 2: Data Ingestion (30-60 minutes)

- [ ] Run fetch: `NEODEMOS_CITY=groningen python scripts/fetch_notulen.py`
- [ ] Run ingest: `NEODEMOS_CITY=groningen python scripts/ingest_data.py`
- [ ] Verify database: Check `meetings` table has `city='groningen'`

### ✅ Phase 3: Data Quality (15 minutes)

- [ ] Run quality check: `python scripts/fix_notulen_data.py --city groningen`
- [ ] Review results in `output/test_results/notulen_data_fix_groningen.json`
- [ ] Add any wrongly-linked documents to `known_wrong_docs` list

### ✅ Phase 4: Party Profiles (30-45 minutes)

- [ ] Extract party positions: `NEODEMOS_CITY=groningen python scripts/extract_party_positions.py`
- [ ] Creates `data/profiles/party_profile_groenlinks_pvda_groningen.json`
- [ ] Verify profile has at least 15+ policy areas

### ✅ Phase 5: Testing (15 minutes)

- [ ] Set env: `export NEODEMOS_CITY=groningen`
- [ ] Start server: `python main.py`
- [ ] Test homepage: `curl http://localhost:8000/` (should show Groningen meetings)
- [ ] Test API: `curl http://localhost:8000/api/analyse/party-lens/1?party=GroenLinks-PvdA`
- [ ] Test database directly to verify city filters work

### ✅ Phase 6: Documentation (10 minutes)

- [ ] Update this guide with Groningen additions
- [ ] Document any city-specific quirks or data issues
- [ ] Add to list of supported cities at top of guide

---

## Key Design Principles

### 1. **No Hard-Coded City Names**
- ❌ WRONG: `if city == "rotterdam": ...`
- ✅ RIGHT: Read from `CityConfig` or environment variable

### 2. **All City Logic in One Place**
- CityConfig in `scripts/fix_notulen_data.py` is single source of truth
- All services read config from there
- Changes to city metadata = one place to edit

### 3. **Database Isolation**
- Each city's data tagged with city name
- Queries filter by `WHERE city = ?`
- No data bleeding between cities

### 4. **Parameterized Imports**
- Party profiles load dynamically: `data/profiles/party_profile_{party}_{city}.json`
- Templates load city-appropriate data
- API returns city-specific results

### 5. **Environment-Driven Behavior**
- `NEODEMOS_CITY` env var = deployment configuration
- Same code runs all cities - just change env var
- Easy to Docker containerize with different env for each city

---

## Common Questions

### Q: Can I run multiple cities simultaneously?
**A**: Currently, the system runs one city per deployment (controlled by `NEODEMOS_CITY`). To run multiple cities simultaneously:
1. Deploy separate Docker containers with different `NEODEMOS_CITY` values
2. Each connects to same PostgreSQL database (data is isolated by city)
3. Or use reverse proxy to route to different containers by path (e.g., `/rotterdam/`, `/amsterdam/`)

### Q: How do I handle multi-level government (city + districts)?
**A**: Add `district` column to `meetings` table. Then:
```python
'rotterdam': {
    'districts': ['Centrum', 'Delfshaven', 'Feijenoord', ...],
    # ...
}
```
Database queries filter by `city` AND `district`.

### Q: What if party names differ by city?
**A**: Extend `CityConfig`:
```python
'rotterdam': {
    'parties': {
        'green': 'GroenLinks-PvdA',
        'liberal': 'VVD',
    },
    # ...
}
```

### Q: How do I handle different ORI indices that change?
**A**: Use a version system:
```python
'rotterdam': {
    'ori_indices': {
        '2024': 'ori_rotterdam_20240101',
        '2025': 'ori_rotterdam_20250629013104',  # Current
    },
}
```
Code selects by date: `ORI_INDEX = config['ori_indices'][year]`

### Q: What about city-specific committee structures?
**A**: Already supported in `CityConfig['committees']`. Add more as discovered:
```python
'amsterdam': {
    'committees': [
        'Gemeenteraad',
        'Commissie Grondexploitatie',
        'Commissie Veiligheid',  # Amsterdam-specific
        # ...
    ],
}
```

---

## Next Steps

1. **Add a new city**: Follow the checklist above
2. **Run all tests**: `python -m pytest tests/` (will auto-test all cities in database)
3. **Monitor data quality**: Review `output/test_results/` after each ingest
4. **Expand party coverage**: Add more political parties to `data/profiles/`
5. **Scale deployment**: Use Docker + environment variables for each city

---

## Support

For issues adding a new city:
1. Check `CityConfig` is correct in `scripts/fix_notulen_data.py`
2. Verify ORI API index is valid for that city
3. Check database has records with `city='name'`
4. Review output in `output/test_results/` for error details
5. Ensure `NEODEMOS_CITY` env var is set before running services

---

**Last Updated**: March 1, 2026  
**Maintained By**: NeoDemos Development Team
