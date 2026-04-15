# WS18 — Pricing Analysis & Tier Validation

## TL;DR

v0.2.0-alpha.2 shipped a 3-tier pricing surface (Gratis / Pro €29 / Premium €49). Only the first two are selectable; the third is "binnenkort". Pricing, quota, corpus limits, and MCP gating were set by gut from one conversation on 2026-04-15. This workstream replaces gut with data: collect beta usage + cost, validate or revise each number, then lock.

## Status

`available` — queued for v0.2.1 or after first 20 beta signups, whichever comes first.

## Owner

unassigned

## Dependencies

- WS8f (admin CMS) — to surface the tier spec on landing pages dynamically
- MCP auth (shipped via WS4) — for per-user usage attribution
- WS16 (MCP monitoring) — for tool-call cost instrumentation
- Data: min. 20 beta users using Pro for ≥30 days

## Cold-start prompt

```
You are picking up WS18 — pricing analysis. Read this file, then
docs/handoffs/done/WS4_MCP_DISCIPLINE.md (auth + token model) and
services/subscriptions.py (current tier catalogue). Your job: replace
gut-feel numbers with data-backed ones, then propose a concrete revision
to services/subscriptions.py. Do NOT change quotas until Dennis signs off.
```

## Files to read first

- [services/subscriptions.py](../../services/subscriptions.py) — TIERS dict, the current spec to validate
- [routes/api.py](../../routes/api.py) — quota enforcement points (search for `quota_month`, `mcp_access`)
- [services/mcp_rate_limiter.py](../../services/mcp_rate_limiter.py) — existing rate limit machinery
- `.claude/projects/-Users-dennistak-Documents-Final-Frontier-NeoDemos/memory/project_eval_architecture.md` — ~€120/mo at 500 queries/day infra estimate
- [templates/abonnement.html](../../templates/abonnement.html) — public pricing page
- [templates/settings.html](../../templates/settings.html) — tier picker UI

## The 6 numbers to validate

| # | Parameter | Current value | Data needed |
|---|---|---|---|
| 1 | Pro price | €29/mo | Conversion rate at €19/€29/€39 beta survey |
| 2 | Premium price | €49/mo | WTP interview data from 3+ power users (target: raadsleden with full-fractie budget) |
| 3 | NB questions/month | 3 | Drop-off analysis — % of anon visitors who hit limit and convert vs bounce |
| 4 | KB questions/month | 50 | P50/P90 actual monthly usage on beta. If P90 > 50, raise; if P50 < 10, lower |
| 5 | Corpus pages limit (50/500/2000) | gut | Observed upload distribution from first 20 users |
| 6 | OD "extra feature" ROI | wekelijkse briefing + watchlist | Interview: which is the actual paid reason? |

## Build tasks

### Phase 1 — Instrumentation (1-2 days)

1. Add `usage_events` table via Alembic:
   - `user_id`, `event_type` (search/mcp_tool), `tool_name`, `input_tokens`, `output_tokens`, `cost_usd_est`, `occurred_at`
   - Indexed on `(user_id, occurred_at)` for monthly aggregation
2. Write `services/usage_tracker.py` — single ingest fn called from `routes/api.py` on search + from `@logged_tool` in `mcp_server_v3.py` on tool dispatch
3. Nightly rollup `scripts/usage_rollup.py` — aggregates per-user monthly usage + cost, stores in `users.monthly_usage_json` (cheap cache)

### Phase 2 — Data collection (≥30 days after Phase 1 live)

4. Wait for N ≥ 20 beta users × 30 days of data
5. Build `/admin/usage` dashboard (extend WS8f admin CMS):
   - Per-user P50/P90/P99 search count per month
   - Per-user MCP tool-call count per month
   - Cost per user (Jina + Anthropic + infra share)
   - Corpus pages uploaded (when personal-corpus ships)

### Phase 3 — WTP survey + interviews (1 week)

6. In-app survey on /settings for every active Pro user: "Wat zou je maandelijks willen betalen voor deze tier? €19 / €29 / €39 / Gratis ok". Store in `subscription_survey_responses`.
7. 1:1 interviews (min. 3) with power users (>80% of KB monthly quota used): "Wat zou je de volgende tier laten activeren voor €49/mo? Briefing? Meer docs? API? Team-sharing?"

### Phase 4 — Analysis + recommendation (1 day)

8. Write `reports/WS18_pricing_analysis.md` with:
   - Per-tier cost per user (actual, not modeled)
   - WTP distribution per tier
   - Usage distribution (P50/P90/P99)
   - Recommendation: for each of the 6 numbers, keep / adjust / re-test
9. Implementation-ready diff for `services/subscriptions.py`

### Phase 5 — Rollout (staged)

10. Apply adjustments in a new Alembic migration + `subscription_tier_history` rollback trail
11. Grandfather existing users: if we raise €29 → €39, existing KB users keep €29 locked for 12 months
12. A/B test new numbers on new signups only for 4 weeks before full rollout

## Acceptance criteria

- [ ] `usage_events` ingested for ≥30 consecutive days
- [ ] ≥20 KB users with full usage profile
- [ ] WTP survey response rate ≥50% of active KB users
- [ ] Per-user cost accuracy within 10% of Anthropic/Jina invoice total
- [ ] Recommendation signed off by Dennis before code change
- [ ] Grandfather logic tested on staging with 3 synthetic users

## Eval gate

| Metric | Target |
|---|---|
| Revenue per active KB user / month | ≥ €29 post-beta |
| Churn at price change | < 20% |
| Cost per KB user / month | < €8 (infra + LLM + Jina combined) |
| NB → KB conversion rate at quota exhaustion | ≥ 5% |

## Risks specific to this workstream

- **Survey bias**: Early beta users are friends/peers and over-report WTP. Weight interviews over surveys.
- **Quota-gaming**: Users refreshing pages to avoid counts. Current enforcement is client-only notice; real gating is per-query backend check.
- **Anthropic price changes**: Sonnet API cost halved between Jan and Apr 2026. Our cost model must be version-pinned, not assumed stable.
- **Jina TPM budget exhaustion**: already noted in `project_jina_budget_priority.md` — a spike above 1.8M TPM triggers cascading tool failures. Rate-limit _before_ price-changing KB quota upward.

## Future work

- Multi-seat / team pricing for fracties (shared corpus + shared quota)
- Annual pricing (20% discount)
- Student/non-profit tier (€0 but Gratis-quota)
- Usage-based overages (pay €0.50 per extra search above KB quota instead of upgrading)

## Related

- [WS19](WS19_PROFILE_RICH.md) — partij + commissies re-introduction (removed from settings 2026-04-15 pending data; will feed back into the topic_description injection path)
