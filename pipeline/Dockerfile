# Latent Sky — GPU forecast image.
#
# Baked BEFORE renting anything (Architecture.md §9.4): the earth2studio install is
# genuinely hostile (makani not on PyPI, torch-harmonics pinned to a git fork, CUDA
# extensions compiled from source) and compiling it on a metered GPU is how a weekend
# disappears. Build and push this from a laptop; the rented instance only pulls and runs.
#
#   docker build -t latentsky-forecast:0.17.0 pipeline/
#   docker build --build-arg BAKE_MODELS=1 -t latentsky-forecast:0.17.0-models pipeline/
#
# BAKE_MODELS=1 additionally downloads both checkpoints into the image (~7.6 GB extra:
# SFNO 6.87 GiB + CorrDiff 684 MiB) so a fresh instance never spends paid minutes on
# NGC downloads. The un-baked variant expects EARTH2STUDIO_CACHE to be pre-seeded from
# S3 by the launch userdata instead (see infra/gpu/prefetch-models.sh).

FROM nvcr.io/nvidia/pytorch:26.04-py3

# GRIB decoding needs eccodes at the apt level; git for the pinned installs.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libeccodes-dev git \
    && rm -rf /var/lib/apt/lists/*

# uv is mandatory: NVIDIA's install guide states extras are only supported via uv with
# GitHub as the source — pip cannot resolve the [tool.uv.sources] pins for
# torch-harmonics/makani. Bare tag, no "v" prefix (a "@v0.17.0" ref does not exist).
RUN pip install --no-cache-dir uv

ENV FORCE_CUDA_EXTENSION=1 \
    TORCH_CUDA_ARCH_LIST="8.0 8.6 8.9 9.0+PTX" \
    EARTH2STUDIO_CACHE=/cache/earth2studio \
    EARTH2STUDIO_PACKAGE_TIMEOUT=3600 \
    HF_HOME=/cache/hf

WORKDIR /opt/latentsky
RUN uv init --python 3.13 --no-workspace . \
    && uv add "earth2studio[sfno,corrdiff] @ git+https://github.com/NVIDIA/earth2studio.git@0.17.0" \
    && uv add zarr numcodecs pyyaml

# The forecast module, configs, and the bake helper.
COPY src/latentsky/forecast.py /opt/latentsky/forecast.py
COPY tools/bake_models.py /opt/latentsky/bake_models.py
COPY configs/ /opt/latentsky/configs/

# Optional model bake. Kept late so config/code edits rarely invalidate it.
ARG BAKE_MODELS=0
RUN if [ "$BAKE_MODELS" = "1" ]; then uv run python /opt/latentsky/bake_models.py; fi

ENTRYPOINT ["uv", "run", "python", "/opt/latentsky/forecast.py"]
CMD ["--config", "/opt/latentsky/configs/event_doksuri_2023.yaml"]
