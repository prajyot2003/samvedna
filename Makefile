.PHONY: test test-verbose verify-audit purge clean

test:
	python3 -m pytest tests/ -q

test-verbose:
	python3 -m pytest tests/ -v

# Re-walks the stored audit ledger and recomputes every hash. Non-zero exit on
# a broken chain, so it can gate CI as well as be demonstrated live.
verify-audit:
	python3 scripts/verify_audit.py

# Applies the raw-audio retention policy and records the purge in the ledger.
purge:
	python3 -c "from services.store.repo import Repository; \
	r = Repository(); print('purged', r.purge_expired_audio(), 'audio blobs')"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache
