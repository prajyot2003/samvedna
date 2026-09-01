.PHONY: test test-verbose test-asr dev readiness fairness evidence verify-audit purge fetch-models validate-asr reference-server clean

# Runs the whole pipeline on SQLite with an in-process bus: no services needed.
dev:
	python3 scripts/run_api.py

# Whether this build may take live calls, and why not.
readiness:
	python3 -c "from services.nlp.lexicon import production_ready; \
	ok, b = production_ready(); print('production ready:', ok); \
	[print('  BLOCKER', x) for x in b]"

test:
	python3 -m pytest tests/ -q

test-verbose:
	python3 -m pytest tests/ -v

# The recognition tests, which need downloaded weights and real recordings.
# They skip elsewhere; run them on a machine with normal internet before making
# any claim about recognition accuracy.
test-asr:
	python3 -m pytest tests/test_asr.py -v -k "not stub"

fetch-models:
	python3 scripts/fetch_models.py --model small

validate-asr:
	python3 scripts/validate_asr.py --telephony

# Serves the ULCA contract locally so the Bhashini client can be developed and
# integration-tested before credentials are issued.
reference-server:
	python3 -m services.asr.reference_server

# Regenerates evidence/FAIRNESS.md from the database. Reports "NO DATA" until
# the shadow-mode pilot has run — which is the honest state, not a bug.
fairness:
	python3 scripts/fairness_report.py

# Everything a reviewer should be handed.
evidence: fairness
	@echo
	@ls -1 evidence/
	@echo
	@python3 -c "from services.nlp.lexicon import production_ready; \
	ok, b = production_ready(); print('production ready:', ok); \
	[print('  BLOCKER', x) for x in b]"

verify-audit:
	python3 scripts/verify_audit.py

purge:
	python3 -c "from services.store.repo import Repository; \
	r = Repository(); print('purged', r.purge_expired_audio(), 'audio blobs')"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache
