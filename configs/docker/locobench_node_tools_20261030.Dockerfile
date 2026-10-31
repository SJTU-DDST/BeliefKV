ARG NODE_IMAGE
ARG BASE_IMAGE
FROM ${NODE_IMAGE} AS node_toolchain
FROM ${BASE_IMAGE}

COPY --from=node_toolchain /usr/local/bin/node /usr/local/bin/node
COPY --from=node_toolchain /usr/local/lib/node_modules/ /usr/local/lib/node_modules/
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx \
    && node --version \
    && npm --version \
    && python3 --version
