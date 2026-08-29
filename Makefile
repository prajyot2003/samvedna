.PHONY: test test-verbose test-asr verify-audit purge fetch-models validate-asr reference-server clean

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

verify-audit:
	python3 scripts/verify_audit.py

purge:
	python3 -c "from services.store.repo import Repository; \
	r = Repository(); print('purged', r.purge_expired_audio(), 'audio blobs')"

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache
