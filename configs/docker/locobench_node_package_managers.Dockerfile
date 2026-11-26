ARG BASE_IMAGE=beliefkv-typescript-tools:20261031
FROM ${BASE_IMAGE}

ENV COREPACK_HOME=/opt/beliefkv-corepack
RUN npm install --global --prefix /usr/local --ignore-scripts --no-audit --no-fund corepack@0.31.0 \
    && /usr/local/bin/corepack enable --install-directory /usr/local/bin \
    && /usr/local/bin/corepack install --global --cache-only pnpm@8.6.3 pnpm@8.6.5 \
    && /usr/local/bin/corepack install --global pnpm@8.15.9 yarn@1.22.22 \
    && chmod -R a+rX "${COREPACK_HOME}"
ENV COREPACK_ENABLE_NETWORK=0 COREPACK_ENABLE_AUTO_PIN=0
