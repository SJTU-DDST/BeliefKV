FROM beliefkv-typescript-tools:20261031

LABEL beliefkv.base_image="sha256:d575c45c64ab3e213cc41620b11d5cab81d9047cacac0d5178b1f706615c174b"
LABEL beliefkv.node="18.20.8"
LABEL beliefkv.scope="runtime compatibility only; no project source or test repairs"

RUN npm install --prefix /opt/beliefkv-node18 --no-audit --no-fund --fetch-timeout=60000 --fetch-retries=1 node@18.20.8
RUN ln -sf /opt/beliefkv-node18/node_modules/node/bin/node /usr/local/bin/node
ENV PATH="/opt/beliefkv-node18/node_modules/.bin:${PATH}"

RUN node -e "if(process.version !== 'v18.20.8') throw Error(process.version); console.log(process.version)" \
    && env PATH=/usr/local/bin:/usr/bin:/bin node -e "if(process.version !== 'v18.20.8') throw Error(process.version)" \
    && npm --version \
    && tsc --version \
    && ts-node --version
