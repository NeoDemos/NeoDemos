# IMPORTANT: GroenLinks-PvdA as Unified Party

## Critical Understanding

**Current Reality (March 2026):**
- GroenLinks and PvdA have merged
- Treated as ONE party: GroenLinks-PvdA
- Formal legal merger: June 2026
- **Users view them as unified NOW**

**Therefore:**
- ✅ All proposals/positions analyzed as ONE party
- ✅ Voting records analyzed as ONE party
- ✅ Budget priorities analyzed as ONE party
- ✅ Notulen statements analyzed as ONE party
- ❌ NEVER compare GL vs PvdA separately
- ❌ NEVER treat as two different political actors

## Implementation Details

### In Proposal Extraction (Step 3A)
- ✓ Already done correctly - extracted from "GroenLinks-PvdA Verkiezingsprogramma"
- Programme is unified 2025-2030 plan

### In Notulen Analysis (Step 3C - Starting NOW)
- MUST search for: "groenlinks" OR "pvda" OR "partij van de arbeid"
- MUST treat all mentions as ONE party
- MUST aggregate all voting/responses together
- NOT separate analysis by party name

**Example:**
```
WRONG: "PvdA voted YES on motion, GroenLinks abstained"
RIGHT: "GroenLinks-PvdA position varied - this suggests internal debate or different speakers"

WRONG: Create separate analysis streams
RIGHT: Unified analysis: "GroenLinks-PvdA collectively took this position"
```

### In Trend Analysis (Step 3D)
- Count ALL references to GroenLinks-PvdA together
- "GroenLinks-PvdA mentions: 12x"
- NOT "GroenLinks: 8x, PvdA: 4x"

### In Final Report (Step 5)
- Header: "GroenLinks-PvdA" (unified name)
- Subtitle: "(Formele fusie juni 2026, maar politieke eenheid nu)"
- All analysis assumes unified party

## Why This Matters for Your PoC

Users in March 2026 think of them as:
- ✓ One party ("GroenLinks-PvdA")
- ✓ One set of promises (unified programme)
- ✓ One set of voting records (council members use unified party name)

If you separate them artificially, you:
- ✗ Confuse users ("Wait, are they two parties or one?")
- ✗ Misrepresent council dynamics (they vote together now)
- ✗ Undermine credibility (shows you don't understand current politics)

## Action Items for Step 3C

When building notulen position inference:

1. ✅ Search query: `"groenlinks" OR "pvda" OR "partij van de arbeid"` (case-insensitive)
2. ✅ Aggregate results: Treat all matches as one party
3. ✅ Output: "GroenLinks-PvdA positions inferred from notulen"
4. ✅ Note variations: "Internal discussion between GL-PvdA members" if different speakers
5. ❌ Never: Create separate GL vs PvdA metrics

## Files to Update

```
Step 3C - notulen_position_inference_service.py
  └─ Search party_pattern = "groenlinks|pvda|partij van de arbeid"
  └─ Output: Unified GroenLinks-PvdA positions
  
Step 3D - trend_analysis_service.py
  └─ Count: All GL-PvdA mentions together
  └─ Report: "GroenLinks-PvdA asked housing X times"
  
Step 4 - proposal_comparison_service.py
  └─ Always: "GroenLinks-PvdA vs College B&W"
  
Step 5 - Final Report
  └─ Header: "GroenLinks-PvdA Politieke Analyse"
```

---

**Going forward: ONE unified party throughout all analysis.**

