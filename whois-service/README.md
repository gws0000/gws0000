# whois-service

Internal directory lookup. A worker mirrors Entra ID users and groups (with direct
memberships) into Postgres using Graph delta queries; a FastAPI app serves lookups.
One image, two roles: `uvicorn main:app` and `python sync_worker.py`.

## Files

| File | Role |
|---|---|
| `main.py` | API: `/whois/*` (API key), `/admin/*` (Authentik), `/healthz` |
| `models.py` | `users`, `groups`, `group_members`, `sync_state` |
| `sync.py` | Delta sync logic (users, then groups) |
| `sync_worker.py` | Worker loop; `--check` is its healthcheck |
| `graph.py` | Graph client: client-credentials token, 429/5xx retry with `Retry-After` |
| `auth.py`, `config.py`, `db.py`, `schemas.py` | Plumbing |

## API

| Method | Path | Notes |
|---|---|---|
| GET | `/whois/users?q=&department=&include_disabled=&limit=&offset=` | `q` matches name, mail, UPN |
| GET | `/whois/users/{id\|upn\|mail}` | Includes direct groups |
| GET | `/whois/groups?q=` | |
| GET | `/whois/groups/{id}` | Members, with user name/mail where known |
| GET | `/admin/sync` | Per-resource sync status and last error |
| POST | `/admin/sync?full=true` | Worker runs on its next poll (30s); `full` drops delta links |

API key: `X-API-Key: <key>` or `Authorization: Bearer <key>`. `WHOIS_API_KEY` accepts a
comma-separated list for rotation. Admin requires `X-authentik-uid`, membership of an
`ADMIN_ALLOWED_GROUPS` group (from `X-authentik-groups`), and the `X-Proxy-Secret`
Traefik injects when `ADMIN_PROXY_SHARED_SECRET` is set. Fails closed if no group is set.

## Sync behaviour

- First run (or after `full=true` or an expired delta token, HTTP 410) is a full sync;
  rows not returned are purged afterwards.
- Later runs use the stored `deltaLink`. Only returned properties are written, so
  partial delta payloads don't blank columns.
- Pages are committed one at a time; a crash mid-round restarts from the last good link.
- Users failing doesn't stop groups, and vice versa. The error lands in `sync_state.last_error`.
- Schema is created on startup (`create_all` under a Postgres advisory lock). Switch to
  Alembic once the schema needs to change.

## Entra app registration

Application permissions, admin-consented: `User.Read.All`, `GroupMember.Read.All`.

## Deploy

CI (`.gitea/workflows/build.yml`) runs ruff and pytest, then pushes
`platform/whois-service:<sha>` and `:latest`. On each node:

```bash
cp .env.example .env   # fill in
docker compose pull && docker compose up -d
```

## Local dev

```bash
uv venv --python 3.12 .venv && . .venv/bin/activate
uv pip install -e ".[dev]"
pytest && ruff check . && ruff format --check .
```
