ARG NODE_IMAGE=beliefkv/locobench-node20-managers:20261124
ARG PYTHON_IMAGE=beliefkv/locobench-python311-tools:20261124
FROM ${NODE_IMAGE} AS node
RUN cp -L /usr/local/bin/node /tmp/beliefkv-node

FROM ${PYTHON_IMAGE}
COPY --from=node /tmp/beliefkv-node /usr/local/bin/node
COPY --from=node /usr/local/lib/node_modules /usr/local/lib/node_modules
COPY --from=node /opt/beliefkv-corepack /opt/beliefkv-corepack
ENV COREPACK_HOME=/opt/beliefkv-corepack COREPACK_ENABLE_NETWORK=0 COREPACK_ENABLE_AUTO_PIN=0
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && ln -s ../lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack \
    && /usr/local/bin/corepack enable --install-directory /usr/local/bin
