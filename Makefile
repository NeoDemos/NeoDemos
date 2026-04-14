# NeoDemos operator targets.
#
# Coordination layer + MCP monitoring shortcuts for Dennis.
# Phony targets only — no real build artifacts here.

.PHONY: help status rebuild reseed archive-dry mcp-watch mcp-stats mcp-alert-test tunnel

help:
	@echo "NeoDemos operator commands:"
	@echo ""
	@echo "  Coordination"
	@echo "    make status         - read .coordination/state.md (auto-generated dashboard)"
	@echo "    make rebuild        - regenerate .coordination/state.md from events.jsonl"
	@echo "    make reseed         - DESTRUCTIVE: wipe events.jsonl + reseed from dependencies.yaml"
	@echo "    make archive-dry    - dry-run batch archive of all WSs in 'done' status"
	@echo ""
	@echo "  MCP monitoring (requires SSH tunnel — run 'make tunnel' first)"
	@echo "    make mcp-watch      - live tail of mcp_audit_log (ctrl-c to stop)"
	@echo "    make mcp-stats      - last-24h summary of MCP activity"
	@echo "    make mcp-alert-test - send a test macOS notification from alert.py"
	@echo ""
	@echo "  Infra"
	@echo "    make tunnel         - start SSH tunnel to Hetzner in background"
	@echo ""
	@echo "Run 'make <target>' to execute."

status:
	@cat .coordination/state.md

rebuild:
	@python3 scripts/coord/rebuild_state.py

reseed:
	@echo "This wipes events.jsonl and reseeds from dependencies.yaml."
	@read -p "Continue? [y/N] " ans; [ "$$ans" = "y" ] || { echo "aborted."; exit 1; }
	@python3 scripts/coord/seed_from_handoffs.py --force-all
	@python3 scripts/coord/rebuild_state.py

archive-dry:
	@echo "Dry-run archive of all WSs currently in 'done' status."
	@echo "Reading state.md for candidates..."
	@python3 -c "import yaml,json,subprocess,sys; \
	from pathlib import Path; \
	events=[json.loads(l) for l in Path('.coordination/events.jsonl').read_text().splitlines() if l.strip()]; \
	done={}; \
	[done.update({e['ws']:True}) if e.get('event')=='completed' else done.pop(e['ws'],None) if e.get('event')=='qa_rejected' else None for e in sorted(events,key=lambda x:x['ts']) if e.get('ws')]; \
	wss=sorted(done); \
	print(' '.join('--ws '+w for w in wss)) if wss else sys.exit('No done WSs to archive.')" | \
	xargs python3 scripts/coord/archive_ws.py --dry-run --allow-dirty

mcp-watch:
	@python3 scripts/mcp/watch.py

mcp-stats:
	@python3 scripts/mcp/stats.py

mcp-alert-test:
	@python3 scripts/mcp/alert.py --test "NeoDemos alert test ($(shell date +%H:%M:%S))"

tunnel:
	@./scripts/dev_tunnel.sh --bg
