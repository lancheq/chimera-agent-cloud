# =============================================================================
# Chimera Agent Baseline
#
# Local:
#   make install                    - editable install with dev deps
#   make run                        - run the agent over all tasks under data/
#   make test                       - run pytest
#   make lint / make format         - ruff
#
# Docker (Grand Challenge):
#   make gc-build                   - build the GC image
#   make gc-test INPUT=<dir>        - run the image on every case in <dir>
#   make gc-save                    - export image + model tarballs for upload
#
# Maintainer-only cluster targets live in Makefile.cluster (auto-included
# when present; safe to delete).
# =============================================================================

-include .env
-include Makefile.cluster

PROJECT_SLUG    ?= chimera_agent_baseline
GC_IMAGE_TAG    ?= chimera-agent-baseline
RUN_ARGS        ?=

.PHONY: help install fetch-embedding-model process-guidelines run test lint format lock gc-build gc-test gc-save clean doctor

help: ## Show this help
	@grep -hE '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

# =============================================================================
# Local development
# =============================================================================

install: ## Install project in editable mode with dev deps
	uv pip install -e ".[dev]"

install-vllm: ## Install the in-process vLLM backend for local GPU runs (Linux GPU box only)
	uv pip install -e ".[dev,vllm]"

fetch-embedding-model: ## Download the RAG embedding model into resources/ (uses the committed guidelines DB as-is)
fetch-reranker: ## Download the bge-reranker-base cross-encoder into resources/ (offline container prep)
	python scripts/download_reranker.py
	python scripts/download_embedding_model.py

process-guidelines: ## Maintainer-only: rebuild the guidelines DB + model from the source PDF (needs docs/internal/guidelines.pdf)
	python scripts/process_guidelines.py

run: ## Run the agent locally over all tasks under data/ (RUN_ARGS for Hydra overrides)
	python -m chimera_agent_baseline.run $(RUN_ARGS)

test: ## Run tests
	pytest tests/ -v

lint: ## Lint + format-check
	ruff check src/ tests/
	ruff format --check src/ tests/

format: ## Auto-format and auto-fix
	ruff format src/ tests/
	ruff check --fix src/ tests/

# Base image the container builds on. KEEP IN SYNC with the Dockerfile FROM.
# Used by `make lock` to compute the project dependency delta on top of it.
BASE_IMAGE ?= vllm/vllm-openai@sha256:6d8429e38e3747723ca07ee1b17972e09bb9c51c4032b266f24fb1cc3b22ed8f

lock: gc-build ## Regenerate requirements.lock (project deps resolved on top of the pinned base image)
	@docker run --rm --entrypoint pip $(BASE_IMAGE) freeze | sort > /tmp/chimera-base-freeze.txt
	@docker run --rm --entrypoint pip $(GC_IMAGE_TAG) freeze | sort > /tmp/chimera-built-freeze.txt
	@{ echo "# Project dependency closure, fully pinned for reproducible builds."; \
	   echo "#"; \
	   echo "# These are the packages the project layer installs/upgrades ON TOP OF"; \
	   echo "# the base image pinned in the Dockerfile. Together — pinned base image"; \
	   echo "# + this lockfile — a build is deterministic."; \
	   echo "#"; \
	   echo "# Regenerate with \`make lock\` (keep Makefile BASE_IMAGE in sync with the"; \
	   echo "# Dockerfile FROM)."; \
	   comm -13 /tmp/chimera-base-freeze.txt /tmp/chimera-built-freeze.txt | grep -v '^chimera_agent_baseline'; \
	 } > requirements.lock
	@echo "Wrote requirements.lock ($$(grep -vc '^#' requirements.lock) pinned packages)"

# =============================================================================
# Grand Challenge container
# =============================================================================

gc-build: ## Build the GC Docker image
	docker build --platform=linux/amd64 --tag $(GC_IMAGE_TAG) .

INPUT ?= data

gc-test: gc-build ## Run the agent image against INPUT=<data_root> (task<N>/agent_input/...), all tasks
	@mkdir -p test/output && chmod 777 test/output
	@# The container writes outputs as its non-root user (UID 999), so a plain
	@# host `rm` of a previous run's nested files fails with permission denied.
	@# Clean inside a throwaway root container instead.
	@docker run --rm --user 0 --entrypoint sh \
		--volume $(CURDIR)/test/output:/clean \
		$(GC_IMAGE_TAG) -c 'rm -rf /clean/* /clean/.[!.]* 2>/dev/null' || true
	docker run --rm \
		--network none \
		--gpus all \
		--volume $(CURDIR)/$(INPUT):/input:ro \
		--volume $(CURDIR)/test/output:/output \
		--volume $(CURDIR)/model:/opt/ml/model:ro \
		$(GC_IMAGE_TAG)
	@echo ""
	@echo "Output files:"
	@ls -la test/output/

gc-save: gc-build ## Export GC image + model as tarballs for upload
	docker save $(GC_IMAGE_TAG) | gzip -c > $(GC_IMAGE_TAG).tar.gz
	@echo "Saved $(GC_IMAGE_TAG).tar.gz ($$(du -h $(GC_IMAGE_TAG).tar.gz | cut -f1))"
	@if [ -n "$$(ls -A model/ 2>/dev/null)" ]; then \
		tar -czf model.tar.gz -C model . && \
		echo "Saved model.tar.gz ($$(du -h model.tar.gz | cut -f1))"; \
	else \
		echo "model/ is empty — skipping model.tar.gz"; \
	fi

# =============================================================================
# Misc
# =============================================================================

doctor: ## Check that local prerequisites are working
	@printf "  %-30s" "Python" && python3 --version 2>/dev/null && echo "" || echo "not found"
	@printf "  %-30s" "uv" && uv --version 2>/dev/null || echo "not found"
	@printf "  %-30s" "Project importable" && python3 -c "import $(PROJECT_SLUG)" 2>/dev/null && echo "ok" || echo "run make install"
	@printf "  %-30s" "Docker" && docker --version 2>/dev/null || echo "not found"
	@printf "  %-30s" "GPU" && nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "no NVIDIA GPU"

clean: ## Remove local build artifacts and caches
	rm -rf build/ dist/ *.egg-info src/*.egg-info
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete 2>/dev/null || true
	@echo "Cleaned local artifacts."
