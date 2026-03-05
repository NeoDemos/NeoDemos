# Revised Approach: Proposal-Level Comparison

## 🎯 What Changed

You identified two critical issues with the original approach:

1. **Too High-Level**: Ideology profiles miss the specific policy details needed for meaningful comparison
2. **Wrong Model**: Gemini Flash 2.5 isn't optimal for complex multi-document comparison

## ✅ New Strategy

Instead of: `Ideology → Topics → Stances`

Build: `Proposals → Details → Comparisons → Alignment`

### Architecture

```
GroenLinks-PvdA Programma (Dutch)
├── Extract Concrete Proposals (Dutch)
│   └── 20+ specific policy proposals with:
│       - Volledige tekst (exact text)
│       - Begroting (budget if stated)
│       - Timeline (2025-2030)
│       - Doelniveau (gemeente/provincie/landelijk)
│       - Implementatiemechanisme (HOW)
│       - Related proposals
│
College B&W Raadsvoorstel (Dutch)
├── Extract from Database
│   └── Actual 2024-2025 proposals with:
│       - Titel
│       - Begroting
│       - Datum ingediend
│       - Uitkomst (aangenomen/verworpen)
│
Initiatiefvoorstel (Dutch)
├── Council member proposals with:
│   - Wethouder responses
│   - Timing of responses
│   - Agreement/disagreement signals
│
Compare & Analyze
├── Proposal-to-proposal matching
├── Values alignment (implicit)
├── Proposal alignment (explicit)
├── Confidence reporting
└── Generate: "Policy Alignment Report"
```

## 🔧 What Was Built

Three new services, all in **Dutch**:

### 1. `proposal_extraction_service.py`
- Extracts detailed proposals from GroenLinks programme
- Captures: titel, beleidsterrein, volledige_tekst, begroting, timeline, doelniveau, implementatiemechanisme, stakeholders, gerelateerde_voorstellen
- Output: Structured proposal database (JSON)

### 2. `raadsvoorstel_extraction_service.py`
- Queries database for actual City Council proposals
- Extracts: College B&W raadsvoorstel + Initiatiefvoorstel
- Parses: títulos, budgets, policy areas, outcomes
- Maintains links to: meeting dates, alderman responses, voting outcomes

### 3. `proposal_comparison_service.py`
- Compares GroenLinks proposals vs actual raadsvoorstel
- Three comparison types:
  1. **Values alignment**: Do underlying principles match?
  2. **Proposal alignment**: Are the concrete proposals similar?
  3. **Outcome tracking**: Did College B&W adopt similar ideas?
- Output: Comparison matrix with confidence scores

## 🚀 Key Advantages

### Better Proof-of-Concept
- **Specific**: "GroenLinks promises 5,000 social housing units; College proposes 500 mixed-income units"
- **Measurable**: Clear differences in ambition, target population, implementation
- **Grounded**: Tied to actual Dutch administrative concepts (raadsvoorstel, wethouder, etc.)

### Dutch Language Precision
- Preserves nuance of Dutch legal/administrative language
- No translation loss
- More credible for Dutch stakeholders

### Gemini Flash 3 Ready
- When Flash 3 becomes available (likely April 2025)
- Can load entire programme + multiple raadsvoorstel in one context
- Do nuanced side-by-side analysis
- Get better confidence on complex policy tradeoffs

## 📊 Expected Outputs

### Proposal-Level Comparison Report

```
VOORSTEL 1: Klimaat - Duurzame energietransitie
- GroenLinks:   "100% hernieuwbare energie door 2030"
- College B&W:  "50% hernieuwbare energie door 2030"
- Alignment:    CONFLICTING (0.3)
- Verschil:     College doelstelling veel minder ambitieus
- Confidence:   95%

VOORSTEL 2: Wonen - Betaalbare huisvesting
- GroenLinks:   "5,000 sociale huurwoningen + huurprijsregulering"
- College B&W:  "500 gemengde inkomens via markt"
- Alignment:    CONFLICTING (0.2)
- Verschil:     GroenLinks veel meer interventionistisch
- Confidence:   92%

VOORSTEL 3: Onderwijs - Inclusieve scholen
- GroenLinks:   "Anti-racisme curriculum + LGBTQ+ safety"
- College B&W:  "Niet expliciet genoemd"
- Alignment:    UNRELATED (0.0)
- Verschil:     College niet gericht op dit onderwerp
- Confidence:   85%
```

## 🔄 Implementation Timeline

### Phase 1: Proposal Extraction (3-4 hours)
- [ ] Run proposal_extraction_service on GroenLinks programme
- [ ] Extract 20-30 detailed proposals in Dutch
- [ ] Save to: `groenlinks_pvda_proposals.json`

### Phase 2: Raadsvoorstel Extraction (2-3 hours)
- [ ] Run raadsvoorstel_extraction_service
- [ ] Parse meeting documents for actual proposals
- [ ] Extract 20-30 College B&W + Council proposals
- [ ] Save to: `raadsvoorstel_2024_2025.json`

### Phase 3: Comparison & Analysis (2-3 hours)
- [ ] Run proposal_comparison_service
- [ ] Generate alignment matrix
- [ ] Create visualization of divergence
- [ ] Save to: `policy_alignment_report.json`

### Phase 4: Gemini Flash 3 Migration (1-2 hours when available)
- [ ] Switch from Flash 2.5 to Flash 3 when released
- [ ] Reload comparison prompts with larger context
- [ ] Improve accuracy on complex policy tradeoffs

## ❓ Decision Points

### 1. Should we start immediately?
**Recommendation: YES**
- Services are ready
- API calls are inexpensive (free tier)
- Can start with Flash 2.5, upgrade to Flash 3 later
- No blocking dependencies

### 2. Do you want to include initiatiefvoorstel (Council proposals)?
**Recommendation: YES**
- Shows GroenLinks' ideas when they're the opposition
- Tracks Council response times
- More complete picture of alignment

### 3. Should we track Wethouder responses?
**Recommendation: YES**
- Shows if College B&W accepts/rejects Council ideas
- Indicates political dynamics
- More nuanced than just voting outcomes

### 4. What time period should we analyze?
**Recommendation: 2024-2025 (current)**
- Covers post-GroenLinks/PvdA merger
- Most relevant for current political situation
- Can expand historically if needed

## 📁 Files Generated So Far

```
services/
├── proposal_extraction_service.py       ← Extract GL proposals
├── raadsvoorstel_extraction_service.py  ← Extract City Council proposals
└── proposal_comparison_service.py       ← Compare & analyze

REVISED_APPROACH_SUMMARY.md              ← This file
```

## ✨ Next Steps

1. **Confirm approval** for this revised approach
2. **Decide on scope**: initiatiefvoorstel? Wethouder responses? Historical data?
3. **Start Phase 1**: Proposal extraction from GroenLinks programme
4. **Monitor Gemini Flash 3**: Switch when available for better analysis

---

## Why This Is Better for Your PoC

**Original approach**: "GroenLinks is center-left, progressive"
- ✓ Accurate but vague
- ✗ Doesn't help you compare specific policies

**New approach**: "GroenLinks proposes 5,000 social housing units; College proposed 500"
- ✓ Specific, measurable, Dutch-language correct
- ✓ Shows real policy divergence
- ✓ Actionable for stakeholders
- ✓ Proof that AI can do meaningful policy analysis, not just classification

This is the kind of analysis your stakeholders will actually use.

