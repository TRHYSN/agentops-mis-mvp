# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS dependencies
WORKDIR /opt/agentops/ui/next-app
COPY historical-source/ui/next-app/package.json historical-source/ui/next-app/package-lock.json ./
RUN npm ci --ignore-scripts

FROM dependencies AS build
ARG AGENTOPS_HISTORICAL_SOURCE_REVISION
COPY historical-source/ui/next-app/ ./
COPY historical-source/migrations/postgres /opt/agentops/migrations/postgres
COPY deploy/byoc/historical-v9-adapter/identity.json ./scripts/byoc-historical-v9-identity.json
COPY deploy/byoc/historical-v9-adapter/schema-identity.mjs ./scripts/byoc-historical-v9-schema-identity.mjs
COPY deploy/byoc/historical-v9-adapter/database-identity.mjs ./scripts/byoc-historical-database-identity.mjs
COPY deploy/byoc/historical-v9-adapter/install.mjs /usr/local/lib/agentops/install-historical-v9-adapter.mjs
RUN node /usr/local/lib/agentops/install-historical-v9-adapter.mjs \
      /opt/agentops/ui/next-app \
      /opt/agentops/ui/next-app/scripts/byoc-historical-v9-identity.json \
      "${AGENTOPS_HISTORICAL_SOURCE_REVISION}" \
    && npm run byoc:schema-identity --silent \
    && npm run build

FROM node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS runtime
ARG AGENTOPS_HISTORICAL_SOURCE_REVISION
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
LABEL org.opencontainers.image.revision="${AGENTOPS_HISTORICAL_SOURCE_REVISION}" \
    io.agentops.byoc.schema-contract="agentops_commercial_postgres_v9" \
    io.agentops.byoc.historical-adapter="agentops_byoc_historical_schema_adapter_v1"
RUN test "$(id -u node)" = "1000" \
    && test "$(id -g node)" = "1000"
WORKDIR /opt/agentops
COPY --from=build /opt/agentops/ui/next-app ./ui/next-app
COPY historical-source/migrations/postgres ./migrations/postgres
COPY --chmod=0555 \
    deploy/byoc/historical-v9-adapter/secret-entrypoint.mjs \
    /usr/local/lib/agentops/historical-v9-secret-entrypoint.mjs
WORKDIR /opt/agentops/ui/next-app
RUN npm prune --omit=dev --ignore-scripts \
    && chown -R node:node /opt/agentops
USER node
EXPOSE 3001
CMD ["node", "scripts/start.mjs"]
