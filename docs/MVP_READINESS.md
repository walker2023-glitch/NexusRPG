# Nexus RPG MVP readiness checklist

This checklist defines the smallest customer-facing proof that the coherent story engine works end to end on a local Docker/homelab deployment.

## Automated gates

- Backend tests pass in GitHub Actions.
- Frontend production build passes in GitHub Actions.
- Docker Compose configuration passes in GitHub Actions.
- `./test_smoke.sh` uses fail-fast HTTP checks and exits nonzero on an API failure.

## Manual acceptance flow

Run the stack with the models available, then run:

```bash
BASE_URL=http://<reachable-api-host>:8000 ./test_smoke.sh
```

Confirm the following:

1. The API health endpoint reports healthy.
2. Architect extraction creates graph nodes and edges.
3. World state can be read back after extraction.
4. Actor mode creates a scene and returns a Chronos commit ID.
5. The Chronos timeline contains that commit.
6. Choose a branch and generate another scene from its commit.
7. Fork a second branch from an earlier commit.
8. Change location on one branch and confirm the other branch is unchanged.
9. Checkout a commit, restart the API/container, and confirm the checked-out world state remains available.
10. Open the frontend from a second device using the configured `NEXT_PUBLIC_API_URL` and `FRONTEND_ORIGINS`.

## MVP boundaries

This MVP is ready for local/small-household use when the acceptance flow passes. It is not yet a multi-user hosted product: authentication, user accounts, and tenant-level authorization remain future work. Keep API and frontend ports limited to the trusted LAN/Tailscale network until those controls exist.

## Recovery

Before destructive maintenance such as `make clean`, copy:

- `data/world_state.json`
- `data/axiom_rules.json`
- the `nexus-chronos` Docker volume
- the `nexus-lancedb` Docker volume

Chronos now fails closed on a corrupted index instead of silently treating the history as empty.
