# Contributing to ProtoLabel

Thanks for considering a contribution. ProtoLabel is a self-hosted, GPU-accelerated
labeling tool for images and video frames (bbox / segment / skeleton / 3D cuboid),
built as an alternative to CVAT/Roboflow for teams that need to keep data and
models on their own infrastructure.

## Before you start

- For anything beyond a small fix, please open an issue first describing the
  problem or proposal. This avoids duplicated work and lets us agree on the
  approach before you invest time in a PR.
- Read `README.md` (Vietnamese, primary) or `README.en.md` (English) for the
  full architecture: FastAPI + SQLite backend, React/Vite frontend, Ultralytics
  YOLO for prelabeling, Docker Compose for deployment.

## Development setup

```bash
# Backend
python -m pip install -r backend/requirements.txt
cp -n .env.example .env   # edit PROTOLABEL_HOST_WORKSPACE before running

# Frontend
npm --prefix frontend ci
npm --prefix frontend run dev   # dev server with hot reload

# Or run both together
./scripts/run_all.sh
```

## Tests

Every backend change should keep the existing suite green, and new endpoints
or behavior changes should come with a new test case in the same file:

```bash
cd backend
python -m pytest tests/ -q
```

For anything that touches authentication, CORS, or the reverse-proxy path
(`scripts/run_all.sh`, `nginx.conf`, `vite.config.js`), please also verify the
change against a real login → save-annotation round trip through whichever
proxy you changed — `TestClient`-based tests call the FastAPI app directly and
will not catch proxy-layer issues (this is exactly how a CSRF/Origin bug in
the dev-server proxy path went unnoticed for a release; see git history).

## Code style

- **Python**: standard library + FastAPI conventions already used in
  `backend/app/`. Keep new SQL parameterized (`?` placeholders) — no string
  interpolation into queries.
- **JavaScript/JSX**: run Prettier before committing once `.prettierrc` is
  added to the repo (tracked as a follow-up). Until then, match the existing
  formatting style in `frontend/src/`.
- Prefer small, focused functions over new abstractions; this codebase favors
  explicit SQL and direct FastAPI route handlers over ORMs/service layers.

## Security-sensitive areas

Please flag (in the PR description, or privately first if it's a live
vulnerability) any change touching:

- `backend/app/auth.py` — session cookies, password hashing, CSRF/Origin
  checks, login rate limiting.
- `safe_root()` / media serving in `backend/app/main.py` — path-traversal
  guards for the workspace filesystem.
- The `remote-models.json` external model API integration — this lets the
  backend proxy uploaded images to a configured HTTP endpoint; changes here
  should not let the frontend influence which endpoint is called.

## Submitting a PR

1. Fork, branch from `main`.
2. Keep commits scoped — one logical change per commit is easier to review
   than a single commit mixing a feature and unrelated cleanup.
3. Describe *why*, not just *what*, in the PR description.
4. Be ready to iterate — this is an early-stage project and review may ask
   for changes.

## Reporting a security issue

Please do not open a public issue for a security vulnerability. Open a
[GitHub Security Advisory](https://github.com/DuyhocAI/ProtoLabel_Tool/security/advisories/new)
on this repository instead, or contact the maintainer directly.
