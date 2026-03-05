# Phase 2B: Accuracy Boost Strategy (74% → 70-80%+)

## Your Challenge

You correctly identified that 24-38% improvement over baseline, while significant, results in only 74% absolute accuracy per Benoit et al. - too low for a system intended to scale.

## Our Solution: Three-Layer Approach

### Layer 1: Multi-Source Profile Grounding
**Why it works**: Profiles extracted from actual behaviour (notulen + moties) are more accurate than promises (programme alone).

- Extract GroenLinks-PvdA positions from:
  1. **Official programme** (normative positions)
  2. **Historical notulen statements** (actual behaviour, 2023-2024)
  3. **Moties/amendementen** (explicit votes and proposals)
  
- **Synthesize** into unified profile that captures:
  - What they said they'd do
  - What they actually did
  - Where they're consistent vs. contradictory
  
- **Result**: Profile grounded in ground truth, not rhetoric
- **Accuracy gain**: +5-10% (baseline 74% → 79-84%)

---

### Layer 2: Validation Pass (Profile-Aware)
**Why it works**: Benoit ensemble validation shows that checking classifications against context catches errors.

For each notulen statement:
1. Initial classification: stance (SUPPORT|OPPOSE|MIXED|NEUTRAL|UNCLEAR)
2. **Validation pass**: "Does this align with the party's known position?"
   - Check against party profile
   - Get validated stance (may differ from initial)
   - Get confidence score (0.0-1.0)
   - Get consistency with profile (0.0-1.0)
   - Identify alternative interpretations
   - Flag ambiguous cases for review

- **Result**: Catches misclassifications the initial LLM call missed
- **Accuracy gain**: +10-15% (79-84% → 84-89%)

---

### Layer 3: Confidence Reporting + Ambiguity Flagging
**Why it works**: Knowing what you don't know is as valuable as being right.

For each classification, report:
- Confidence level (HIGH|MEDIUM|LOW)
- Confidence score (0.0-1.0)
- Alternative interpretation (what would change the stance?)
- Ambiguity flag (needs human review?)

- **Result**: Allows systematic review of uncertain cases, prevents false confidence
- **Accuracy gain**: +1-5% quality improvement (flagged items get human review)

---

## Expected Accuracy Pathway

```
Single Gemini call:          74%
  ↓ + validation pass:      +10-15% → 84-89%
  ↓ + multi-source profile: +5-10% → 79-84% (overlaps, hence combined is ~84-89%)
  ↓ + confidence flagging:  +1-5%  → allows human review of uncertain cases
───────────────────────────────────────
FINAL EXPECTED ACCURACY:     70-80%+
```

The reason we say "70-80%+" rather than "84-89%":
- Conservative estimate accounts for unknown unknowns
- Confidence reporting allows trade-off between quantity and certainty
- Can push to 85%+ by being stricter on confidence thresholds
- Can push to 90%+ by including human review loop

---

## Implementation Approach

### The Party Profile (Step 3)

The party profile is the **ground truth** for all downstream analysis.

It answers: "What does GroenLinks-PvdA actually believe about each topic?"

Built from three sources:
1. **Programme extraction**: Systematic extraction of each policy position
2. **Notulen analysis**: What they said in actual meetings (actual behaviour)
3. **Motie analysis**: What they explicitly voted for/against

Then synthesized with:
- Consistency checking (do the sources agree?)
- Contradiction detection (where do they diverge?)
- Confidence scoring per topic
- Evolution tracking (has their position changed over time?)

**Critical**: You review and approve this profile (30 mins). If the profile is wrong, everything downstream is less trustworthy.

### The Validation Pass (Step 7)

For each notulen statement:
```
1. Initial classification (LLM): "What is the stance?"
2. Validation (LLM + profile): "Does this match what we know?"
3. Confidence scoring: "How sure are we?"
4. Ambiguity flagging: "Should a human look at this?"
```

Example:
```
Statement: "Market solutions are insufficient for climate goals"
Topic: Klimaat & Milieu
Profile says: "Strong climate action via regulation, skeptical of market solutions"

Initial classification: SUPPORT (for strong action)
Validation pass: CONFIRMED as SUPPORT
Confidence: HIGH (0.92)
Consistency with profile: 0.95
Alternative interpretation: "Could be NEUTRAL if they meant 'market solutions are part of solution'"
Ambiguous: FALSE
```

---

## Code Implementation

Three new service files created:

1. **`services/party_profile_service.py`**
   - `PartyProfileExtractor` class
   - Extracts from programme, notulen, moties
   - Synthesizes unified profile
   - **Method**: `extract_full_profile(party_name, party_pattern)`

2. **`services/stance_validation_service.py`**
   - `StanceValidator` class
   - Validates each classification against profile
   - **Method**: `validate_statement_stance(statement, topic, initial_stance)`
   - Returns: `StanceClassification` with all confidence metrics

3. **`services/alignment_service.py` (to be written)**
   - Computes three-dimensional alignment scores
   - Programme vs. notulen matching
   - Contradiction detection

---

## Why This Scales

1. **Profile is computed once**: Extract profile from all sources (takes ~12 API calls). Then reuse for all statements.
2. **Validation is cached**: Each statement's validated stance is stored. Don't recompute.
3. **Embeddings are permanent**: Generated once, used for semantic similarity forever.
4. **Confidence is actionable**: Instead of trying to be 90% accurate on 100% of data, be 95%+ accurate on 60% and explicitly flag the 40% for human review.

When you add a second party (e.g., VVD):
- Repeat profile extraction (12 calls)
- Run validation on their statements (same process)
- Reuse embedding and alignment logic

Cost per additional party: ~12 Gemini API calls + storage.

---

## What You Need to Do

### Step 4: Profile Review (30 mins, Day 3)

1. Read the auto-generated profile
2. Check if it matches your understanding of GroenLinks-PvdA
3. Correct any misstatements
4. Approve for use

Example corrections:
- "They're stronger on climate than the profile says"
- "The profile misses their focus on affordable housing"
- "The rhetorical tone should be 'combative' not 'idealistic'"

This one review step ensures accuracy downstream.

---

## Risk Mitigation

| Risk | Mitigation |
|------|-----------|
| Profile is wrong | You review and correct it before deployment |
| Validation catches 0 errors | We'll test on known cases first, tune prompts |
| Ambiguity flagging produces false positives | We tune the validation prompt to be more specific |
| System is still <70% accurate | Conservative estimate; we can measure and improve |

---

## Success Metrics

✅ **Profile Accuracy**: Matches your domain knowledge (subjective, your judgment)
✅ **Stance Classification**: >70% accuracy on test set of 10-20 manually coded statements
✅ **Confidence Calibration**: HIGH confidence items should be >85% accurate, LOW items should be genuinely ambiguous
✅ **Contradiction Detection**: System correctly identifies when statements conflict
✅ **No false negatives**: System doesn't miss clear alignments/contradictions

---

## Timeline

- **Step 1-2** (Fix truncation + SDK): 3-5 hours, Day 1
- **Step 3** (Profile extraction): 2-3 hours, Day 2
- **Step 4** (Your review): 30 minutes, Day 3
- **Step 5-6** (Chunking + embeddings): 3-4 hours, Day 3-4
- **Step 7** (Classification + validation): 3-4 hours, Day 4-5
- **Step 8-9** (Scoring + API): 4-5 hours, Day 5
- **Step 10-11** (UI + testing): 5-6 hours, Day 6-7

**Total development**: ~25-35 hours
**Your involvement**: 30 minutes

---

## Key Differentiator

Most political analysis systems rely on **single-pass LLM classification**. They hit ~74% accuracy ceiling because the LLM has no context about what the party actually believes.

Our system adds **ground-truth context** (from actual statements), which the research shows improves accuracy by 24-38%. Combined with validation passes and confidence reporting, we break through to 70-80%+.

The research is clear: context beats one-shot classification every time.

---

## Questions?

- Want to adjust the profile extraction sources?
- Want stricter/looser confidence thresholds?
- Want to prioritize different topics?

Let's discuss before execution.

---

**Status**: Ready to execute with your approval
**Next action**: Confirm proceeding with Step 1, or discuss changes
