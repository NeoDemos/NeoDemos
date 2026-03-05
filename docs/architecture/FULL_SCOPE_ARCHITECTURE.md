# Full-Scope Policy Analysis Architecture

## 🎯 Three-Layer Analysis (You're Right)

The problem: College B&W's real policy direction isn't always in formal raadsvoorstel. It's *implied* by:
- What they allocate budget to
- How they respond to initiatives
- What they discuss repeatedly in notulen
- Their voting patterns
- Their Wethouder statements

### Layer 1: Formal Proposals
**Source**: Raadsvoorstel & Initiatiefvoorstel
```
Type: Explicit
Format: Official documents
Credibility: Highest
Examples: 
  - "500 housing units, mixed-income"
  - "Invest €50M in public transport"
```

### Layer 2: Implicit Positions (From Notulen)
**Source**: Meeting minutes, Wethouder responses
```
Type: Inferred from behavior
Format: Statements, responses, voting
Credibility: High (actual behavior)
Examples:
  - College prioritizes market solutions (see 3 housing votes)
  - Wethouder consistently responds "already in market" to GL initiatives
  - Budget allocation shows priority: Climate < Housing < Economie
```

### Layer 3: Trend Signals
**Source**: Pattern analysis across all notulen
```
Type: Aggregated signals
Format: Frequency analysis, sentiment
Credibility: Medium-High (aggregate beats single statements)
Examples:
  - "College frames housing as market issue" (mentioned 12 times)
  - "GL frames housing as social problem" (mentioned 18 times)
  - Divergence score: 0.75 (high conflict)
```

## 🔧 Implementation

### Updated `raadsvoorstel_extraction_service.py`
Now extracts THREE things:

```python
class PolicyPosition:
    """Any articulated policy position - formal or implicit"""
    
    # Layer 1: Formal
    id: str
    titel: str
    soort: str  # "raadsvoorstel" / "initiatiefvoorstel" / "impliciete_positie"
    formeel_voorstel: bool  # Is it official?
    
    # Layer 2: Implicit (from notulen)
    source_type: str  # "raadsvoorstel" / "notulen_response" / "stemgedrag" / "budget"
    context_notulen_id: str  # Which meeting revealed this?
    volledige_tekst: str
    
    # Layer 3: Confidence
    formaliteit: float  # 0 (inferred) to 1 (official)
    confidence: float  # Based on evidence count
    evidence_count: int  # How many times observed?
```

### Updated `proposal_comparison_service.py`
Now does THREE-WAY comparison:

```
VOORSTEL: Wonen - Betaalbare huisvesting
────────────────────────────────────────

LAYER 1: FORMEEL (Raadsvoorstel)
- GroenLinks initiatiefvoorstel:
  "5,000 sociale huurwoningen + huurprijsregulering"
  
- College raadsvoorstel:
  "500 gemengde inkomens via markt"
  
- Formeel alignment: 0.2 (sterk conflicterend)

LAYER 2: IMPLICIET (Uit Notulen)
- College positie (inferred from responses):
  "Markt moet huisvesting oplossen"
  "Sociale huurwoningen 'niet rendabel'"
  "Particuliere ontwikkelaars prioriteit"
  
- GL positie (inferred from proposals & vragen):
  "College geeft markt vrij spel"
  "Bezit wordt probleem voor burgers"
  
- Impliciete alignment: 0.1 (zeer conflicterend)

LAYER 3: TRENDS (Patroonanalyse)
- GL woningvragen in 2024: 12x
- College responses "markt-gericht": 11x
- Budget naar woningbouw: €5M (van €300M)
- Trend conflict score: 0.85 (zeer hoog)

OVERALL ASSESSMENT:
- Formeel conflict: 0.2
- Impliciet conflict: 0.1
- Trend conflict: 0.85
- Average: 0.38 (signficant policy divergence)
- Confidence: 0.92 (veel evidence)
```

## 📊 What Gets Captured

| Type | Example | From Where | Confidence |
|------|---------|-----------|-----------|
| **Formal** | "500 housing units" | Raadsvoorstel | 99% |
| **Implicit** | "College prefers market solutions" | Wethouder response | 85% |
| **Trend** | "GL asks housing 12x/year" | Notulen analysis | 90% |
| **Derived** | "Policy divergence: 0.85" | Aggregation | 92% |

## 🔄 The Pipeline

```
1. Extract Raadsvoorstel
   ↓
2. Extract Initiatiefvoorstel
   ↓
3. Analyze Notulen for Implicit Positions
   ├── Wethouder responses
   ├── Voting patterns
   └── Budget allocation
   ↓
4. Calculate Trend Signals
   ├── Frequency analysis (GL vs College)
   ├── Sentiment analysis (support/oppose)
   └── Aggregate confidence
   ↓
5. Three-Way Comparison
   ├── Formal proposals
   ├── Implicit positions
   └── Trend signals
   ↓
6. Generate Policy Alignment Report (Dutch)
   └── Shows all three layers with confidence
```

## ✨ Why This Works for Your PoC

**Problem**: "College hasn't formally proposed housing policy yet, so we can't compare"

**Solution**: Your system can say:
```
"Although College has no formal raadsvoorstel,
their implicit position is clear from:
- 11 Wethouder responses favoring market
- 90% of housing votes supporting private development
- Budget allocation: <2% to social housing

Compared to GroenLinks' explicit advocacy for:
- Social housing mandate
- Rent control
- Public-driven solutions

Policy divergence: HIGH (0.85)"
```

This is the kind of nuance politicians and policymakers actually need.

## 📁 Services to Build/Update

```
services/
├── proposal_extraction_service.py           ← Keep (formal proposals)
├── raadsvoorstel_extraction_service.py      ← Update (add implicit layer)
├── notulen_position_inference_service.py    ← NEW (find implicit positions)
├── trend_analysis_service.py                ← NEW (pattern signals)
└── proposal_comparison_service.py           ← Update (three-way comparison)
```

## 🚀 Implementation Priority

1. **Phase 1**: Formal proposals (existing code, ready to run)
2. **Phase 2**: Extract notulen positions (new service, ~2 hours)
3. **Phase 3**: Trend analysis (new service, ~2 hours)
4. **Phase 4**: Three-way comparison (update existing, ~1 hour)

**Total**: ~5 hours to full system

---

## Decision: Ready to Build?

Shall I start with Phase 2: Notulen position inference service?

This will scan all Rotterdam Gemeenteraad notulen to extract implicit College B&W policy positions (from their responses, voting, budget actions), even without formal raadsvoorstel.

