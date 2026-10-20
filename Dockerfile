# The geospatial pipeline (satellite, reach, exposure, fuse, validate, trust, serve) on CPU, from the lock file.
# The model stages (SAM 3, Depth Anything, llama.cpp) need weights and are not part of this image.
#
#   docker build -t floodline .
#   docker run --rm -v "$PWD/data:/app/data" -v "$PWD/results:/app/results" floodline validate \
#       data/external/ems/EMSR517_GRA --osm-pbf data/external/osm/koeln-regbez-261018.osm.pbf

FROM python:3.12-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app

COPY requirements/lock-geo-dev.txt requirements/lock-geo-dev.txt
RUN pip install -r requirements/lock-geo-dev.txt

COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN pip install --no-deps . && useradd --create-home --uid 10001 floodline && mkdir -p data results \
    && chown floodline data results
USER floodline
ENV FLOODLINE_ROOT=/app
ENTRYPOINT ["floodline"]
CMD ["--help"]
