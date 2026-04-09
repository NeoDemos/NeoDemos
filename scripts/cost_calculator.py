#!/usr/bin/env python3
"""
Precise cost calculator for metadata enrichment pipeline.
Uses actual chunk sizes from the database, not estimates.
"""

import psycopg2

DB_URL = "postgresql://postgres:postgres@localhost:5432/neodemos"

# Anthropic pricing (per token)
SONNET_IN  = 3.00 / 1_000_000
SONNET_OUT = 15.00 / 1_000_000
HAIKU_IN   = 0.80 / 1_000_000
HAIKU_OUT  = 4.00 / 1_000_000

# Dutch text: ~4 chars per token on average
CHARS_PER_TOKEN = 4


def get_chunk_stats(cur):
    """Get actual chunk sizes from database, grouped by doc_type."""
    cur.execute("""
        SELECT
            CASE
                WHEN d.name ~* 'motie' THEN 'motie'
                WHEN d.name ~* 'amendement' THEN 'amendement'
                WHEN d.name ~* 'notulen|verslag' THEN 'notulen'
                WHEN d.name ~* 'raadsvoorstel|collegevoorstel' THEN 'raadsvoorstel'
                WHEN d.name ~* 'begroting|jaarstuk|jaarrekening|voorjaarsnota' THEN 'financieel'
                ELSE 'overig'
            END as doc_type,
            COUNT(*) as chunk_count,
            AVG(LENGTH(dc.content)) as avg_chars,
            PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY LENGTH(dc.content)) as median_chars,
            PERCENTILE_CONT(0.9) WITHIN GROUP (ORDER BY LENGTH(dc.content)) as p90_chars,
            SUM(LENGTH(dc.content)) as total_chars
        FROM document_chunks dc
        JOIN documents d ON dc.document_id = d.id
        WHERE dc.content IS NOT NULL
        GROUP BY 1
        ORDER BY chunk_count DESC
    """)
    return {row[0]: {
        "count": row[1], "avg_chars": row[2], "median_chars": row[3],
        "p90_chars": row[4], "total_chars": row[5]
    } for row in cur.fetchall()}


def calculate_costs(stats):
    print("=" * 80)
    print("PRECISE COST CALCULATION - Based on actual DB chunk sizes")
    print("=" * 80)

    # Print chunk size summary
    print("\nActual chunk sizes from database:")
    print(f"  {'Type':<15} {'Count':>10} {'Avg chars':>10} {'Median':>8} {'P90':>8}")
    print(f"  {'-'*15} {'-'*10} {'-'*10} {'-'*8} {'-'*8}")
    for dtype, s in sorted(stats.items(), key=lambda x: -x[1]["count"]):
        print(f"  {dtype:<15} {s['count']:>10,} {s['avg_chars']:>10,.0f} "
              f"{s['median_chars']:>8,.0f} {s['p90_chars']:>8,.0f}")

    # What we send per call
    SYSTEM_PROMPT = 300    # tokens: Dutch extraction instructions
    DOC_METADATA = 80      # tokens: doc name, date, committee, type
    REGISTRY_CONTEXT = 200 # tokens: known politician names for this period
    RULE_HINTS = 50        # tokens: pre-extracted parties, speakers, motion IDs
    PROMPT_OVERHEAD = SYSTEM_PROMPT + DOC_METADATA + REGISTRY_CONTEXT + RULE_HINTS

    # Output sizes (tokens)
    OUTPUT_FULL = 300      # entities + relationships + plan G fields
    OUTPUT_LIGHT = 120     # plan G fields only (section_topic, key_entities, questions)

    # High-value doc types (use Sonnet)
    hv_types = ["notulen", "motie", "amendement", "raadsvoorstel", "financieel"]
    hv_total = sum(stats[t]["count"] for t in hv_types if t in stats)
    hv_llm_pct = 0.70  # 70% need LLM after rule pass
    hv_llm_calls = int(hv_total * hv_llm_pct)

    # Overig (use Haiku)
    ov_total = stats.get("overig", {}).get("count", 0)
    ov_llm_pct = 0.15  # only 15% need LLM
    ov_llm_calls = int(ov_total * ov_llm_pct)

    print(f"\nLLM calls after rule filtering:")
    print(f"  High-value: {hv_total:,} chunks x {hv_llm_pct:.0%} = {hv_llm_calls:,} Sonnet calls")
    print(f"  Overig:     {ov_total:,} chunks x {ov_llm_pct:.0%} = {ov_llm_calls:,} Haiku calls")

    scenarios = [
        ("A: chunk[:500]  (125 tok)", 500),
        ("B: chunk[:2000] (500 tok)", 2000),
        ("C: Full chunk   (avg ~285 tok)", None),  # None = use actual avg
    ]

    for label, char_limit in scenarios:
        print(f"\n{'=' * 80}")
        print(f"SCENARIO {label}")
        print(f"{'=' * 80}")

        # --- HIGH-VALUE (Sonnet) ---
        if char_limit is not None:
            # Capped: use min(avg_chars, char_limit) / CHARS_PER_TOKEN
            hv_content_tokens = char_limit / CHARS_PER_TOKEN
        else:
            # Full: weighted average of actual chunk sizes
            weighted_sum = sum(
                stats[t]["count"] * stats[t]["avg_chars"]
                for t in hv_types if t in stats
            )
            hv_content_tokens = (weighted_sum / hv_total) / CHARS_PER_TOKEN

        hv_input_per_call = PROMPT_OVERHEAD + hv_content_tokens
        hv_total_input = int(hv_llm_calls * hv_input_per_call)
        hv_total_output = hv_llm_calls * OUTPUT_FULL
        hv_cost = hv_total_input * SONNET_IN + hv_total_output * SONNET_OUT

        print(f"\n  HIGH-VALUE (Sonnet 4.6)")
        print(f"  Calls:         {hv_llm_calls:>12,}")
        print(f"  Input/call:    {hv_input_per_call:>12,.0f} tokens "
              f"(overhead={PROMPT_OVERHEAD} + content={hv_content_tokens:.0f})")
        print(f"  Output/call:   {OUTPUT_FULL:>12,} tokens")
        print(f"  Total input:   {hv_total_input:>12,} tokens  -> ${hv_total_input * SONNET_IN:>10.2f}")
        print(f"  Total output:  {hv_total_output:>12,} tokens  -> ${hv_total_output * SONNET_OUT:>10.2f}")
        print(f"  Subtotal:      ${hv_cost:>10.2f}")

        # --- OVERIG (Haiku) ---
        if char_limit is not None:
            ov_content_tokens = char_limit / CHARS_PER_TOKEN
        else:
            ov_content_tokens = stats.get("overig", {}).get("avg_chars", 1184) / CHARS_PER_TOKEN

        ov_overhead = SYSTEM_PROMPT + DOC_METADATA + RULE_HINTS  # no registry for overig
        ov_input_per_call = ov_overhead + ov_content_tokens
        ov_total_input = int(ov_llm_calls * ov_input_per_call)
        ov_total_output = ov_llm_calls * OUTPUT_LIGHT
        ov_cost = ov_total_input * HAIKU_IN + ov_total_output * HAIKU_OUT

        print(f"\n  OVERIG (Haiku 4.5)")
        print(f"  Calls:         {ov_llm_calls:>12,}")
        print(f"  Input/call:    {ov_input_per_call:>12,.0f} tokens "
              f"(overhead={ov_overhead} + content={ov_content_tokens:.0f})")
        print(f"  Output/call:   {OUTPUT_LIGHT:>12,} tokens")
        print(f"  Total input:   {ov_total_input:>12,} tokens  -> ${ov_total_input * HAIKU_IN:>10.2f}")
        print(f"  Total output:  {ov_total_output:>12,} tokens  -> ${ov_total_output * HAIKU_OUT:>10.2f}")
        print(f"  Subtotal:      ${ov_cost:>10.2f}")

        total = hv_cost + ov_cost
        print(f"\n  COMBINED:      ${total:>10.2f}")
        print(f"  + 30% retries: ${total * 1.3:>10.2f}")
        print(f"  + 50% safety:  ${total * 1.5:>10.2f}")


if __name__ == "__main__":
    conn = psycopg2.connect(DB_URL)
    cur = conn.cursor()
    stats = get_chunk_stats(cur)
    calculate_costs(stats)
    cur.close()
    conn.close()
