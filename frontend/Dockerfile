# ── Stage 1: install dependencies ────────────────────────────────────────────
FROM node:20-bookworm-slim AS deps
WORKDIR /app

COPY package.json ./
RUN npm install --frozen-lockfile=false

# ── Stage 2: build the Next.js production bundle ──────────────────────────────
FROM node:20-bookworm-slim AS builder
WORKDIR /app

COPY --from=deps /app/node_modules ./node_modules
COPY . .
# Keep the runtime COPY deterministic when the app has no public assets yet.
RUN mkdir -p public

ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

# ── Stage 3: minimal production runner ────────────────────────────────────────
FROM node:20-bookworm-slim AS runner
WORKDIR /app

ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1

RUN addgroup --system --gid 1001 nodejs &&     adduser  --system --uid 1001 nextjs

COPY --from=builder /app/public ./public
COPY --from=builder --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/.next/static ./.next/static

USER nextjs
EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"

CMD ["node", "server.js"]
