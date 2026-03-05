# NeoDemos: Correct Architecture & Analytical Framework

**Date Corrected:** March 1, 2026  
**Status:** Architectural redesign required based on user clarification

---

## Core Concept: "Through the Lens of the Party of Your Choice"

NeoDemos analysis evaluates **Rotterdam's actual policies through a chosen party's ideological framework**.

This is **NOT**:
- Comparing all parties objectively
- Making absolute judgments about policies
- Measuring against external standards

This **IS**:
- "From party X's stated perspective, how well does Rotterdam align with their values?"
- "Is party X consistent - are they working toward their stated vision?"
- "Where are the gaps between party's ideology and Rotterdam's actual policies?"

---

## Party Positions & Values Sources

Party positions and values must be derived from **both**:

### 1. Programme Documents
- Official election platforms
- Stated policy positions
- Ideological commitments
- Vision statements

### 2. Notulen (Council Meeting Minutes)
The actual voiced positions in council:
- **Statements made** by party members during debates
- **Motions submitted** (showing proposed alternatives)
- **Questions asked** (showing priorities and concerns)
- **Amendments proposed** (showing specific policy adjustments)
- **Voting behavior** (showing actual choices)
- **All position indicators** from council activity

These two sources combined = **Complete picture of party's stated positions and actual values**

---

## Correct Analysis Flow

### Step 1: Extract Party Positions & Values Profile

**Input:**
- Party programme document (formal stated positions)
- Notulen mentioning party (actual voiced positions in council)

**Process:**
- Extract policy positions on each topic area (housing, climate, etc.)
- Extract stated values and principles
- Extract actual behavior indicators from notulen
- Identify any contradictions or evolutions

**Output:**
```json
{
  "party": "GroenLinks-PvdA",
  "positions": {
    "housing": {
      "programme_position": "Betaalbare huurwoningen, aanpak leegstand",
      "notulen_statements": ["Motion 2023: prevent speculation", "Question 2024: social housing targets"],
      "value": "Housing as right, not commodity",
      "consistency": "Consistent across sources"
    },
    "climate": {
      "programme_position": "Energietransitie, CO2 reduction targets",
      "notulen_statements": ["Amendment 2024: stricter building standards"],
      "value": "Ecological sustainability as priority",
      "consistency": "Consistent"
    }
    // ... all policy areas
  }
}
```

### Step 2: Extract & Categorize Rotterdam Policies

**Input:**
- College B&W proposals/decisions
- Budget allocations
- City council decisions on major policies

**Process:**
- Extract each policy/decision
- Tag with: policy area, content, impacts, timeline
- Organize by ideological category (housing, climate, economy, etc.)

**Output:**
```json
{
  "rotterdam_policies": [
    {
      "id": "ROT-2024-housing-001",
      "title": "Housing development strategy 2024-2026",
      "policy_area": "Wonen",
      "content": "Market-led approach with incentives for affordable units",
      "budget": "€50M",
      "coalition_parties": ["VVD", "D66", "PvdA"],
      "date": "2024-06-15"
    },
    // ... all policies
  ]
}
```

### Step 3: Evaluate Policies Through Party Lens

**Input:**
- Party positions & values profile (from Step 1)
- Rotterdam policies (from Step 2)

**Process:**
For each Rotterdam policy:
1. Compare to party's stated values
2. Measure alignment/divergence
3. Identify gaps
4. Determine if policy moves toward or away from party's vision

**Output:**
```json
{
  "party_lens_analysis": {
    "housing": {
      "policy": "Market-led housing development",
      "party_vision": "Affordable housing as right",
      "alignment_score": 0.35,  // 0.0 = opposite, 1.0 = perfect match
      "assessment": "Policy emphasizes market mechanisms; party vision emphasizes social responsibility",
      "gap": "Market incentives insufficient to achieve affordability targets",
      "party_response": "Submitted amendment proposing rent controls (not adopted)"
    },
    "climate": {
      "policy": "Building standards updated to 2020 EU specifications",
      "party_vision": "Aggressive CO2 reduction, net-zero by 2030",
      "alignment_score": 0.60,
      "assessment": "Standards align with EU but below party's stated targets",
      "gap": "2030 target unachievable with 2020 standards",
      "party_response": "Submitted motion for 2030-aligned standards (defeated)"
    }
    // ... all policy areas
  }
}
```

### Step 4: Analyze Party Consistency

**Input:**
- Party positions from Step 1
- Their actual council behavior (from notulen)
- Their alignment scores (from Step 3)

**Process:**
- Do party's actions match their stated values?
- Are they actively working toward their vision?
  - If in coalition: pushing policies toward their vision?
  - If in opposition: proposing viable alternatives?
- Is there consistency or contradiction?

**Output:**
```json
{
  "consistency_analysis": {
    "overall_coherence_score": 0.78,
    "statement_to_action_consistency": "High - positions consistent with motions submitted",
    "actions_toward_vision": [
      {
        "policy_area": "housing",
        "target": "Affordable housing",
        "actions_taken": ["Motion for rent controls", "Question on social housing targets"],
        "effectiveness": "Low - proposals not adopted"
      }
    ],
    "role_analysis": {
      "status": "Opposition (since 2022 elections)",
      "opposition_effectiveness": "Articulate critique, limited policy impact",
      "consistency_in_opposition": "Consistently proposing alternatives aligned with vision"
    }
  }
}
```

---

## Deliverable: Policy Report Through Party Lens

### Format

**Title:** "Rotterdam Policy Evaluation Through [Party Name]'s Lens"

**Structure:**

```
1. PARTY PROFILE
   ├─ Programme stated positions (by policy area)
   ├─ Actual voiced positions in council (by policy area)
   ├─ Core values & principles
   └─ Stated vision for Rotterdam

2. ROTTERDAM POLICY EVALUATION
   ├─ Housing: Alignment analysis, gaps, party response
   ├─ Climate: Alignment analysis, gaps, party response
   ├─ Economy: Alignment analysis, gaps, party response
   ├─ [All policy areas]
   └─ Overall alignment score

3. PARTY CONSISTENCY ANALYSIS
   ├─ Are stated values reflected in actual behavior?
   ├─ Effectiveness of opposition/coalition role
   ├─ Where party is winning vs losing on their priorities
   └─ Coherence assessment

4. GAPS & DIVERGENCES
   ├─ Major policy areas where Rotterdam diverges from party vision
   ├─ Root causes (coalition compromise, opposing majority, resource constraints)
   └─ Party's actual response to gaps

5. FORWARD OUTLOOK
   ├─ If in opposition: likelihood of implementing vision if elected
   ├─ If in coalition: progress toward vision goals
   ├─ Party trajectory and trend analysis
```

---

## Architecture Flexibility: Change the Lens

The beauty of this architecture is **lens modularity**:

```
USER INTERFACE:
┌─────────────────────────────────────────┐
│ Choose Party: [Dropdown]                │
│ ☐ GroenLinks-PvdA                      │
│ ☐ VVD                                  │
│ ☐ D66                                  │
│ ☐ PvdA                                 │
│ ☐ SP                                   │
│ [Generate Analysis]                    │
└─────────────────────────────────────────┘

When user selects different party:
- Load that party's programme
- Extract that party's notulen positions
- Re-evaluate Rotterdam policies through THAT lens
- Show that party's alignment scores and gaps
- Assess that party's consistency
```

This enables users to:
1. **Understand Rotterdam through any party's perspective**
2. **Compare how different parties view the same policies**
3. **See which party aligns best with a voter's own values**

---

## Key Architectural Changes Required

### Phase 2B (Previous - INCORRECT)
- ❌ Compared GL-PvdA programme to College proposals
- ❌ Treated it as consistency analysis
- ❌ Single party, non-modular

### Phase 2B+ (Corrected - CORRECT)
- ✅ Extract party positions from programme + notulen
- ✅ Evaluate Rotterdam policies through party's lens
- ✅ Measure alignment of city's actual work to party's values
- ✅ Show party's consistency in working toward their vision
- ✅ Multi-party capable (switch lens)

---

## Critical Data Requirements

### Must Have (for any party analysis):
1. **Party programme document** (formal positions)
2. **Notulen from relevant period** (actual voiced positions)
3. **Rotterdam policy documents** (College proposals, budgets, decisions)
4. **Coalition status timeline** (when party was in/out of coalition)

### For GL-PvdA Specifically:
1. ✓ 2025 programme (we have)
2. ⚠️ Post-2022 notulen with correct dates (need to fix)
3. ✓ College B&W proposals (we have)
4. ✓ Coalition status 2022-2026: Opposition (confirmed)

### For Historical Comparison (if needed):
1. GL/PvdA programme from 2018 (if they were in 2018-2022 coalition)
2. What that coalition actually delivered (2018-2022)
3. Notulen from that period

---

## Why This Matters

This architecture transforms NeoDemos from a **comparison tool** into a **perspective tool**:

- **For voters:** "Does this party actually believe what they claim?"
- **For parties:** "Are we consistent with our stated values?"
- **For analysts:** "How do Rotterdam's actual policies align with different ideological visions?"
- **For journalists:** "Which party is most truthful about their priorities?"

The key insight: **Party consistency is measured against their own values, not external standards.**

---

## Next Steps

1. ✓ Confirm this architectural understanding is correct
2. Gather GL-PvdA coalition history (were they in 2018-2022 coalition?)
3. Fix notulen date metadata
4. Build new party position profile service (programme + notulen combined)
5. Build Rotterdam policy evaluation service (policies through party lens)
6. Build party consistency analysis service
7. Generate corrected report for GL-PvdA

This is a **fundamental redesign from Phase 2B**, but with correct architectural foundation.
