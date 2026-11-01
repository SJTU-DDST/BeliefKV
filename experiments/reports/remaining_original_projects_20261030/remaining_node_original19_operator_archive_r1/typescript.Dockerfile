FROM beliefkv-node-base-for-ts:20261031

LABEL beliefkv.base_image="sha256:50fbe7c65f5ae7fd4000597734121af9d511534a72270ec053bc956aa59ebe3b"
LABEL beliefkv.typescript="5.7.2"
LABEL beliefkv.ts_node="10.9.2"

RUN npm install --global --no-audit --no-fund --fetch-timeout=60000 --fetch-retries=1 typescript@5.7.2 ts-node@10.9.2

COPY toolchain_probe.ts /tmp/beliefkv-ts-toolchain-check/toolchain_probe.ts
RUN tsc --strict --target ES2020 --module commonjs --outDir /tmp/beliefkv-ts-toolchain-check/compiled /tmp/beliefkv-ts-toolchain-check/toolchain_probe.ts \
    && node /tmp/beliefkv-ts-toolchain-check/compiled/toolchain_probe.js \
    && ts-node --skip-project --compiler-options '{"module":"CommonJS","target":"ES2020"}' /tmp/beliefkv-ts-toolchain-check/toolchain_probe.ts \
    && tsc --version \
    && ts-node --version \
    && rm -rf /tmp/beliefkv-ts-toolchain-check
