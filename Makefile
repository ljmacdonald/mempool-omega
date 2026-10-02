# Mempool Omega - one-command entry points. Run `make help`.
PY ?= python3
MODE ?= auto

.DEFAULT_GOAL := help
.PHONY: help setup test lint demo train backtest redteam paper paper-synthetic dashboard docker-build docker-paper docker-dashboard clean-demo

help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

setup: ## Install everything (one command)
	$(PY) -m pip install --upgrade pip
	$(PY) -m pip install -e ".[all]"

test: ## Run unit tests
	$(PY) -m pytest

lint: ## Lint
	ruff check .

demo: ## Offline end-to-end demo on SYNTHETIC data (+ charts in docs/screenshots)
	$(PY) -m scripts.demo

train: ## Retrain models (MODE=auto|live|synthetic)
	$(PY) -m omega.train --mode $(MODE)

backtest: ## Walk-forward backtest
	$(PY) -m backtest.engine --mode $(MODE)

redteam: ## Adversarial red-team backtest (synthetic attacks)
	$(PY) -m backtest.redteam

paper: ## One live paper-trading pass (public data)
	$(PY) -m paper.trader --once --mode $(MODE) --capture 30

paper-synthetic: ## One paper pass on synthetic data (offline)
	$(PY) -m paper.trader --once --mode synthetic

dashboard: ## Launch the Streamlit dashboard on http://localhost:8501
	streamlit run dashboard/app.py

docker-build: ## Build the container
	docker compose build

docker-paper: ## Paper pass inside Docker
	docker compose run --rm paper

docker-dashboard: ## Dashboard inside Docker
	docker compose up dashboard

clean-demo: ## Remove demo output
	rm -rf .demo_state
