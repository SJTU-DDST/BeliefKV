ARG PYTHON_IMAGE=python:3.11-slim-bookworm
ARG TOOLS_IMAGE=beliefkv/locobench-agent:tools-only-20261026
FROM ${PYTHON_IMAGE} AS python
FROM ${TOOLS_IMAGE}
COPY --from=python /usr/local /usr/local
