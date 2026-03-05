# NeoDemos - Phase 1a Complete

Rotterdam city council meeting analysis platform powered by Gemini AI.

## Quick Start

### Prerequisites
- PostgreSQL 16+ (installed via `brew install postgresql@16`)
- Python 3.11+
- Gemini API key
- Gmail account (optional, for error notifications)

### Installation & Setup

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Start PostgreSQL
brew services start postgresql@16

# 3. Create database (if not already done)
createdb -U postgres neodemos

# 4. Configure .env
cp .env.example .env  # Then edit with your API keys
```

### Running the Application

```bash
# Start the server (listens on http://localhost:8000)
python main.py

# Server includes:
# - Web UI at http://localhost:8000
# - API endpoints for analysis
# - Daily auto-refresh at 8 AM UTC
# - Error notifications via email
```

## Data Management

### Ingest Meetings
```bash
# Ingest 2024 meetings
python scripts/ingest_data.py --start-date 2024-01-01 --end-date 2024-12-31

# Ingest with meeting minutes (notulen)
python scripts/ingest_data.py --start-date 2024-01-01 --end-date 2024-12-31 --notulen

# Ingest specific month
python scripts/ingest_data.py --start-date 2025-02-01 --end-date 2025-02-28
```

### Validate Data
```bash
# Check database integrity and quality
python scripts/validate_data.py
```

### Run Tests
```bash
# Run all automated tests (12 tests)
python -m pytest tests/ -v

# Run specific test class
python -m pytest tests/test_storage.py::TestStorageBasics -v
```

## Architecture

### Tech Stack
- **Backend**: FastAPI + Python 3.13
- **Database**: PostgreSQL 16
- **AI**: Gemini 2.5 Flash API
- **Frontend**: Jinja2 templates + vanilla JavaScript
- **Scheduling**: APScheduler (daily 8 AM refresh)
- **Testing**: pytest

### Database Schema
```
meetings (84 records)
├── id, name, start_date, committee, location, organization_id
├── Relationships: 1-to-many with agenda_items

agenda_items (473 records)
├── id, meeting_id, number, name
├── Relationships: 1-to-many with documents

documents (1,306 records)
├── id, agenda_item_id, meeting_id, name, url, content, summary_json
└── Full-text content extracted from PDFs

ingestion_log (audit trail)
├── Tracks all data ingestion operations
└── Used for daily refresh detection
```

## API Endpoints

### Web UI
- `GET /` - Homepage with meetings
- `GET /calendar` - Full month calendar view
- `GET /meeting/{meeting_id}` - Meeting detail page

### Analysis API
- `POST /api/analyse/agenda/{agenda_item_id}` - Deep analysis using Gemini

Example response:
```json
{
  "summary": "Executive summary of the agenda item",
  "key_points": ["Proposal 1", "Proposal 2"],
  "conflicts": ["Differing opinions on topic X"],
  "decision_points": ["What needs to be decided"],
  "controversial_topics": ["Sensitive areas"],
  "questions": ["Critical questions for council members"],
  "party_alignment": {
    "score": 85,
    "alignment_level": "Hoog",
    "reasoning": "Why this aligns with party vision"
  }
}
```

## Configuration

### .env File
```bash
# PostgreSQL
DB_HOST=localhost
DB_PORT=5432
DB_NAME=neodemos
DB_USER=postgres
DB_PASSWORD=postgres

# Gemini API
GEMINI_API_KEY=your_api_key_here

# Email (optional, for error notifications)
SMTP_SERVER=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-gmail@gmail.com
SMTP_PASSWORD=your_app_password
ERROR_EMAIL_TO=tak.dpa@gmail.com
```

See `GMAIL_SETUP.md` for Gmail configuration instructions.

## Daily Auto-Refresh

The system automatically runs a daily refresh at **8:00 AM UTC**:

1. Checks for new meetings since last refresh
2. Downloads documents
3. Runs Gemini analysis on substantive items
4. Logs results to `ingestion_log` table
5. Sends error email if refresh fails

To adjust the time, edit in `main.py`:
```python
CronTrigger(hour=8, minute=0)  # Change hour/minute
```

## Troubleshooting

### PostgreSQL Issues
```bash
# Check if PostgreSQL is running
brew services list | grep postgres

# Start PostgreSQL
brew services start postgresql@16

# Connect to database
psql -U postgres -d neodemos
```

### Ingestion Issues
```bash
# Check ingestion logs
SELECT * FROM ingestion_log ORDER BY run_date DESC LIMIT 5;

# Validate data quality
python scripts/validate_data.py
```

### Email Not Sending
```bash
# Check Gmail app password (2FA required)
# Verify SMTP settings in .env
# Test with: python -c "from services.email_service import EmailService; ..."
```

## Data Statistics

```
Current Dataset:
- Meetings:      84 (Jan 2024 - Jan 2025)
- Agenda items:  473
- Documents:     1,306
- Avg size:      6,655 bytes per document
- Data quality:  100% integrity (0 orphaned records)
```

## Testing Results

```
12 Automated Tests: ALL PASSING ✓
├── Connection & Data Access (6 tests)
├── Substantive Item Filtering (2 tests)
├── Document Storage (2 tests)
└── Data Integrity (2 tests)

Execution Time: <1 second
```

## File Structure

```
NeoDemos/
├── main.py                    # FastAPI application
├── requirements.txt           # Python dependencies
├── .env                       # Configuration (not in git)
├── README.md                  # This file
├── GMAIL_SETUP.md            # Email configuration guide
├── PHASE_1A_COMPLETE.md      # Detailed phase summary
│
├── services/
│   ├── storage.py            # PostgreSQL backend
│   ├── open_raad.py          # OpenRaadsinformatie API
│   ├── ai_service.py         # Gemini analysis
│   ├── scraper.py            # PDF text extraction
│   ├── refresh_service.py    # Daily refresh logic
│   └── email_service.py      # Error notifications
│
├── scripts/
│   ├── ingest_data.py        # Data ingestion
│   ├── validate_data.py      # Data validation
│   └── migrate_sqlite_to_postgres.py  # One-time migration
│
├── templates/
│   ├── index.html            # Homepage
│   ├── meeting.html          # Meeting detail
│   └── calendar.html         # Calendar view
│
├── static/
│   └── css/style.css         # Styling
│
└── tests/
    └── test_storage.py       # Unit tests
```

## Development Roadmap

### Phase 1b: Party Vision System
- Upload party programmes
- Extract party positions from past decisions
- Add political alignment scoring

### Phase 2: Mobile App
- iPad/iPhone apps
- CloudKit sync
- Offline access

### Phase 3: Advanced Features
- Stakeholder impact analysis
- Historical precedent research
- Cross-meeting topic tracking

## Contact & Support

- **Issues**: Check PostgreSQL is running and API keys are configured
- **Data**: See `PHASE_1A_COMPLETE.md` for detailed statistics
- **API**: Gemini responses may take 5-10 seconds for large documents

---

**Status**: Phase 1a Complete ✅  
**Last Updated**: 2026-02-28  
**Ready for**: April 2025 council member demo
