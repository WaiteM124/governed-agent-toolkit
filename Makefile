.PHONY: demo test eval lint format clean

# Run the walkthrough against the in-memory store
demo:
	python demo.py

# Run the full test suite
test:
	pytest tests/ -v

# Adversarial eval gate (deterministic; no API key)
eval:
	python -m evals.runner

# Check for lint and formatting issues
lint:
	ruff check .
	ruff format --check .

# Auto-fix lint and format in place
format:
	ruff check --fix .
	ruff format .

# Remove generated artefacts
clean:
	find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .ruff_cache
