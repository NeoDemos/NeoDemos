# NeoDemos Rotterdam RIS Integration - Implementation Checklist

## Pre-Implementation Review

- [x] **Investigation Complete** - Rotterdam RIS at ris.rotterdam.nl is not accessible
- [x] **Solution Identified** - OpenRaadsinformatie (ORI) API is fully operational
- [x] **Data Verified** - 584 recent meetings (2024-2025) confirmed in ORI
- [x] **Architecture Validated** - NeoDemos already uses correct ORI API
- [x] **Documentation Created** - 3 comprehensive guides provided
- [x] **Risk Assessment** - LOW risk for implementation

---

## Phase 1: Dynamic Index Discovery (IMMEDIATE - 1-2 weeks)

### Code Changes

- [ ] **Update `services/open_raad.py`**
  - [ ] Replace hardcoded `INDEX = "ori_rotterdam_20250629013104"`
  - [ ] Add `async def get_latest_rotterdam_index()` function
  - [ ] Implement index refresh logic (daily)
  - [ ] Add error handling for index discovery failures
  - [ ] Add logging for index changes

**Reference Implementation:**
```python
async def get_latest_rotterdam_index():
    """Discover latest Rotterdam index"""
    async with httpx.AsyncClient() as client:
        response = await client.get(
            "https://api.openraadsinformatie.nl/v1/elastic/_cat/indices?format=json"
        )
        indices = response.json()
        rotterdam_indices = [
            idx["index"] for idx in indices 
            if "rotterdam" in idx["index"] and idx["index"].startswith("ori_")
        ]
        return sorted(rotterdam_indices)[-1]

class OpenRaadService:
    def __init__(self):
        self.INDEX = None
        self.index_updated = datetime.utcnow()
    
    async def ensure_latest_index(self):
        if (datetime.utcnow() - self.index_updated).days >= 1:
            self.INDEX = await get_latest_rotterdam_index()
            self.index_updated = datetime.utcnow()
```

### Testing

- [ ] **Unit Tests**
  - [ ] Test dynamic index discovery function
  - [ ] Test meeting query with latest index
  - [ ] Test meeting details fetch
  - [ ] Test error handling for API failures
  - [ ] Verify database schema compatibility

- [ ] **Integration Tests**
  - [ ] Query recent 2024-2025 meetings
  - [ ] Fetch agenda items for a meeting
  - [ ] Get documents for agenda items
  - [ ] Verify text extraction quality
  - [ ] Check data consistency

### Staging Deployment

- [ ] **Environment Setup**
  - [ ] Update `.env` with ORI API endpoint (if needed)
  - [ ] Configure logging for index changes
  - [ ] Set up daily index refresh schedule
  - [ ] Create monitoring dashboard

- [ ] **Data Validation**
  - [ ] Sync 30 days of meetings to staging database
  - [ ] Verify all documents downloaded successfully
  - [ ] Check text extraction for 10+ sample documents
  - [ ] Validate 2024-2025 data completeness

- [ ] **Performance Testing**
  - [ ] Query response time: target <100ms
  - [ ] Concurrent request handling: test 5+ parallel queries
  - [ ] Cache effectiveness: measure hit/miss ratio
  - [ ] Database query performance: verify index usage

### Documentation

- [ ] **Update Code Comments**
  - [ ] Document index discovery function
  - [ ] Add comments for hardcoded -> dynamic migration
  - [ ] Update API endpoint documentation

- [ ] **Update README.md** (if exists)
  - [ ] Add note about ORI as data source
  - [ ] Document index naming pattern
  - [ ] Link to ORI documentation

---

## Phase 2: UI & UX Enhancements (2-4 weeks)

### Voting Records Limitation

- [ ] **Create Disclaimer Component**
  - [ ] Design UI notice about missing voting records
  - [ ] Place on meeting details page
  - [ ] Add tooltip with explanation
  - [ ] Link to future notulen import feature

**Suggested Text:**
> "Decision outcomes are shown below. Detailed voting records for individual councillors are not available in the public API but can be found in official meeting minutes."

### Decision Outcome Extraction

- [ ] **Implement Decision Parser**
  - [ ] Extract decision statements from agenda item names
  - [ ] Parse decision documents for outcomes
  - [ ] Create decision_type mapping (Voorstel -> Proposal, etc.)
  - [ ] Store extracted decisions in database

- [ ] **Decision Display**
  - [ ] Show decision status on agenda item view
  - [ ] Display decision document link
  - [ ] Highlight rejected/amended items

### Full-Text Search UI

- [ ] **Search Interface**
  - [ ] Add search box to main page
  - [ ] Implement keyword search across all documents
  - [ ] Add filters by date, committee, document type
  - [ ] Display search result snippets with context

- [ ] **Party Alignment Search**
  - [ ] Create search form for party keywords
  - [ ] Highlight matching documents
  - [ ] Show summary of aligned vs. opposing content
  - [ ] Export search results

### Caching & Performance

- [ ] **Implement Query Caching**
  - [ ] Cache recent meetings (TTL: 6 hours)
  - [ ] Cache agenda items (TTL: 24 hours)
  - [ ] Cache documents (TTL: 7 days)
  - [ ] Add cache invalidation on sync

- [ ] **Performance Optimization**
  - [ ] Profile slow queries
  - [ ] Add database indices for frequent queries
  - [ ] Implement pagination for large result sets
  - [ ] Optimize text storage (compress old documents)

---

## Phase 3: Advanced Features (1-2 months, optional)

### Alternative Notulen Source

- [ ] **Web Scraping Implementation** (if pursuing)
  - [ ] Identify rotterdam.nl notulen URL patterns
  - [ ] Implement respectful scraper with rate limiting
  - [ ] Extract and store minutes documents
  - [ ] Link with ORI meetings by date/title
  - [ ] Add robots.txt compliance check

- [ ] **Notulen Integration**
  - [ ] Add notulen tab to meeting view
  - [ ] Full-text search across minutes
  - [ ] Extract voting outcomes from minutes
  - [ ] Version control for amended minutes

### Amendment Tracking

- [ ] **Version History Implementation**
  - [ ] Track document updates via ORI `updated` field
  - [ ] Create amendment timeline view
  - [ ] Highlight differences between versions
  - [ ] Show amendment dates and reasons

- [ ] **User Interface**
  - [ ] Display version history on document view
  - [ ] Side-by-side diff viewer for amendments
  - [ ] Timeline visualization
  - [ ] Export amendment log

### Committee Views & Filtering

- [ ] **Committee-Specific Views**
  - [ ] Extract committee list from ORI
  - [ ] Create committee profile pages
  - [ ] Show all meetings for each committee
  - [ ] Committee member listings
  - [ ] Committee decision tracking

- [ ] **Role-Based Views** (future enhancement)
  - [ ] Show relevant items for specific councillors
  - [ ] Party-specific agenda highlighting
  - [ ] Committee assignment visualization

---

## Ongoing Maintenance

### Daily Tasks

- [ ] **Automated Data Sync**
  - [x] Schedule daily API queries for new meetings
  - [x] Sync to local database
  - [x] Log sync errors
  - [ ] Monitor sync latency

- [ ] **Health Monitoring**
  - [ ] Check ORI API availability (uptime)
  - [ ] Verify data freshness
  - [ ] Monitor database size
  - [ ] Alert on failures

### Weekly Tasks

- [ ] **Index Verification**
  - [ ] Check if index name has changed
  - [ ] Update code if index changed
  - [ ] Verify no missed meetings

- [ ] **Data Quality Check**
  - [ ] Sample 5-10 recent documents
  - [ ] Verify text extraction quality
  - [ ] Check for duplicate documents
  - [ ] Validate agenda hierarchies

### Monthly Tasks

- [ ] **Full Re-Index**
  - [ ] Re-sync all meetings from last 90 days
  - [ ] Clean up database duplicates
  - [ ] Verify data consistency
  - [ ] Generate usage statistics

- [ ] **Performance Review**
  - [ ] Analyze query performance metrics
  - [ ] Review API response times
  - [ ] Check cache hit/miss ratios
  - [ ] Plan optimizations for next month

### Alerts & Monitoring

- [ ] **Setup Email Alerts**
  - [ ] ORI API unreachable (severity: HIGH)
  - [ ] Index name changed (severity: MEDIUM)
  - [ ] Data sync failure (severity: HIGH)
  - [ ] Database size exceeds limit (severity: MEDIUM)

- [ ] **Setup Log Monitoring**
  - [ ] Query failures
  - [ ] Index changes
  - [ ] Missing data patterns
  - [ ] Performance degradation

---

## Known Issues & Workarounds

### Issue: Voting Records Unavailable

**Status:** Accepted limitation
**Workaround:** Show decision outcomes from documents
**Future Solution:** Import from rotterdam.nl notulen when scraper implemented
**User Communication:** Documented in UI disclaimer

### Issue: Index Name Is Date-Stamped

**Status:** Being fixed in Phase 1
**Solution:** Dynamic index discovery
**Estimated Fix Time:** 1-2 hours

### Issue: Minutes (Notulen) Incomplete

**Status:** Accepted limitation for Phase 1
**Workaround:** Use agenda items + decision documents
**Future Solution:** Phase 3 optional notulen scraping

### Issue: PDF Text Quality

**Status:** Accepted limitation
**Workaround:** Use for search/summarization, not quotes
**Solution:** Post-process text extraction (clean formatting)

---

## Success Criteria

### Phase 1 Completion

- [x] Dynamic index discovery implemented
- [ ] All 584 recent meetings successfully synced
- [ ] Zero API errors in staging for 7 days
- [ ] Database query response time <100ms
- [ ] Documentation updated

### Phase 2 Completion

- [ ] Voting record disclaimer visible on UI
- [ ] Full-text search functional
- [ ] Decision outcomes extractable
- [ ] Caching reduces API calls by 70%+
- [ ] UI performance: page load <2 seconds

### Phase 3 Completion (Optional)

- [ ] Notulen scraper running successfully
- [ ] Amendment tracking functional
- [ ] Committee views available
- [ ] No unplanned maintenance required

---

## Risk Mitigation

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| ORI API downtime | Low | High | Implement fallback cache, status page |
| Index name change | Medium | High | Dynamic discovery (Phase 1) |
| Data quality issues | Low | Medium | Weekly quality audits |
| Performance degradation | Medium | Medium | Database indexing, query optimization |
| Scope creep | Medium | Low | Enforce phase gates, prioritize features |

---

## Timeline Estimate

| Phase | Duration | Effort | Start | End |
|-------|----------|--------|-------|-----|
| Phase 1 | 1-2 weeks | 8-12 hours | Week 1 | Week 2 |
| Phase 2 | 2-4 weeks | 16-20 hours | Week 3 | Week 6 |
| Phase 3 | 1-2 months | 20-30 hours | Week 7+ | Optional |
| **Total** | **~2 months** | **44-62 hours** | | |

---

## Sign-Off

- **Investigation Date:** February 28, 2025
- **Assessment Status:** COMPLETE
- **Recommendation:** PROCEED WITH PHASE 1
- **Risk Level:** LOW
- **Expected Production Date:** <1 week for Phase 1 completion

---

## References

1. **ROTTERDAM_RIS_ASSESSMENT.md** - Comprehensive technical analysis
2. **ORI_INTEGRATION_GUIDE.md** - Implementation cookbook with code examples
3. **RIS_INVESTIGATION_SUMMARY.txt** - Executive summary and key findings

---

## Questions?

For detailed information on any section, refer to the supporting documentation files or contact the investigation team.

