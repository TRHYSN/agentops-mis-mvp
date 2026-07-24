# syntax=docker/dockerfile:1.7

FROM node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS dependencies
WORKDIR /opt/agentops/ui/next-app
COPY ui/next-app/package.json ui/next-app/package-lock.json ./
RUN npm ci --ignore-scripts

FROM dependencies AS build
COPY ui/next-app/ ./
RUN npm run build

FROM node:22-bookworm-slim@sha256:6c74791e557ce11fc957704f6d4fe134a7bc8d6f5ca4403205b2966bd488f6b3 AS runtime
ENV NODE_ENV=production \
    NEXT_TELEMETRY_DISABLED=1
WORKDIR /opt/agentops
COPY --from=build /opt/agentops/ui/next-app ./ui/next-app
COPY migrations/postgres ./migrations/postgres
WORKDIR /opt/agentops/ui/next-app
RUN npm prune --omit=dev --ignore-scripts \
    && chown -R node:node /opt/agentops
USER node
EXPOSE 3001
CMD ["node", "scripts/start.mjs"]
