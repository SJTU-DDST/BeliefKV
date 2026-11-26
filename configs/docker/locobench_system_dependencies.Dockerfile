ARG BASE_IMAGE=beliefkv/locobench-python311-tools:20261124
FROM ${BASE_IMAGE}

ARG PACKAGES
RUN apt-get update \
    && apt-get install -y --no-install-recommends ${PACKAGES} \
    && rm -rf /var/lib/apt/lists/*
