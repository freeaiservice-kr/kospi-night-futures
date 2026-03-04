.PHONY: install dev test lint check-api clean

install:
	cd backend && pip install -e ".[dev]"

dev:
	uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest backend/tests -v

lint:
	ruff check backend/

check-api:
	python3 scripts/check_api.py

clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null; \
	find . -type f -name "*.pyc" -delete; \
	rm -f backend/kis_token_cache.json
