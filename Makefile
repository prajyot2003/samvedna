.PHONY: test lint clean

test:
	python3 -m pytest tests/ -q

test-verbose:
	python3 -m pytest tests/ -v

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache
