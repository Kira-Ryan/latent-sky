# Latent Sky — task entry points.
# Windows users: run via Git Bash ("make" from Git for Windows or via scoop/choco make).
# Node 22 is required for web targets; if your shell still resolves an older node,
# either run `nvm use 22.21.1` (admin terminal) or prefix:
#   PATH="/c/Users/User/AppData/Roaming/nvm/v22.21.1:$PATH"

NODE22 := /c/Users/User/AppData/Roaming/nvm/v22.21.1

.PHONY: help luts encode-dev budget test-pipeline dev build smoke probes

help:
	@echo "luts           bake 256x1 LUT PNGs from ramps.yaml"
	@echo "encode-dev     encode the local dev sample (data/dev/raw -> data/dev/encoded)"
	@echo "budget         payload gate over data/dev/encoded"
	@echo "test-pipeline  pytest for the encode pipeline"
	@echo "dev            vite dev server (Node 22)"
	@echo "build          vite production build (Node 22)"
	@echo "smoke          headed-Chrome smoke test against the built app"
	@echo "probes         re-run the free probes (2, 3)"

luts:
	python -m latentsky.ramps --config pipeline/configs/ramps.yaml --out pipeline/luts

encode-dev:
	python -m latentsky.encode_dev --raw data/dev/raw --out data/dev/encoded

budget:
	python -m latentsky.budget data/dev/encoded --ceiling-mb 12

test-pipeline:
	cd pipeline && python -m pytest -q

dev:
	cd web && PATH="$(NODE22):$$PATH" npm run dev

build:
	cd web && PATH="$(NODE22):$$PATH" npm run build

smoke:
	cd web && PATH="$(NODE22):$$PATH" node tests/smoke.spec.mjs

probes:
	python probes/probe2_corrdiff_grid.py --root probes || true
	cd probes/probe3-colour-identity && PATH="$(NODE22):$$PATH" node run.mjs
