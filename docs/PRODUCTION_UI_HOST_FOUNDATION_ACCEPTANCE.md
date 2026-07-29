# Production UI Host Foundation Acceptance

## Scope

This is the first executable slice of the Local Host + Remote Console plan. It
adds a production-built, same-origin React UI path while preserving the current
loopback and live-runtime safety boundaries.

This slice is not remote-console completion. Human browser authentication,
private-network publication, installer assets, and second-device acceptance
remain subsequent gates.

## Product Behavior

- `server.py --ui-dist <directory>` serves a production React build and the
  existing API from one origin.
- React's `/mis-api/*` requests are mapped server-side to the canonical
  `/api/*` handlers; canonical `/api/*` clients remain compatible.
- Browser deep links fall back to the React `index.html` without converting API
  errors into HTML.
- Static assets are served as bytes with MIME types and immutable caching;
  `index.html` uses `no-cache` so updates are discoverable.
- `run_local_stack.py --build-ui` builds and starts the same-origin mode.
- `run_local_stack.py --production-ui` reuses an existing build.
- The existing Vite development mode remains available and unchanged.
- Production UI mode rejects an already occupied backend port rather than
  silently attaching to a server that may not serve the requested UI build.
- The launcher still rejects non-loopback backend hosts and still requires
  `--confirm-live-workers` for Hermes/OpenClaw.

## Verification

Commands run on the implementation worktree:

```bash
python3 -m py_compile server.py scripts/run_local_stack.py scripts/production_ui_host_smoke.py
python3 scripts/production_ui_host_smoke.py
python3 scripts/run_local_stack_smoke.py
cd ui/start-building-app && npm run build
git diff --check
```

Results:

- Python compile: passed.
- Production host smoke: passed using an isolated temporary SQLite database and
  temporary static build; root, SPA deep route, asset, `/mis-api` and `/api`
  paths were verified.
- Existing local stack smoke: passed; mock worker registered and live worker
  confirmation remained fail-closed.
- React production build: passed, 2,277 modules transformed.
- `git diff --check`: passed.
- No real runtime, external connector, credential, user database, prompt, or
  response was used by this acceptance.

## Safety Evidence

`scripts/production_ui_host_smoke.py`:

- uses a temporary database and temporary UI directory;
- sets `AGENTOPS_SKIP_SEED_EXPORTS=1`;
- does not run Vite, Hermes, OpenClaw, Dify, or Notion;
- checks output for token-like material;
- verifies both the browser-facing same-origin API prefix and canonical API;
- leaves repository runtime data unchanged.

## Known Limitations

- Human browser login, session cookies, CSRF, roles, and origin enforcement are
  not implemented by this slice.
- Private host mode must therefore remain on loopback.
- The production bundle is built locally and ignored by Git; a reproducible
  release asset has not yet been published.
- The current JavaScript bundle is about 1.66 MB before gzip and emits a Vite
  chunk-size warning. Code splitting is a later performance task, not a blocker
  for the host foundation.
- A second computer has not yet completed an authenticated acceptance run.

## Next Slice

Freeze the human/machine route classification, add a distinct human session
credential and bootstrap-owner flow, then enforce authenticated reads and
role/CSRF checks before private-network publication is enabled.
