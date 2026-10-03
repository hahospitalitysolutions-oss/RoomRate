# Login with Neon Auth

RoomRate signs users in with **Neon Auth** (Better Auth, managed by Neon) on
the same Neon project as the database. Its users live in the `neon_auth`
schema of that database. Supabase is no longer used.

## How it fits together

- **Browser** — `frontend/src/app/services/auth.service.ts` uses
  `@neondatabase/auth` with its Supabase-compatible adapter. The session is a
  cookie on the Neon Auth domain. The session's `access_token` is a Neon Auth
  JWT (EdDSA, 15 minutes, renewed by the SDK), sent to the API as
  `Authorization: Bearer`.
- **API** — `api/services/auth_service.py::NeonAuthService` verifies that JWT
  against `<NEON_AUTH_URL>/.well-known/jwks.json`:
  - It always checks the signature, `exp` and `sub`.
  - It checks `iss` and `aud` only when `NEON_AUTH_JWT_ISSUER` /
    `NEON_AUTH_JWT_AUDIENCE` are set.
  - The account identity is `auth_provider = 'neon_auth'`,
    `auth_subject = <Neon user id>`.

## Configuration

| Where | Setting |
|---|---|
| API `.env` | `NEON_AUTH_URL=https://ep-….neonauth.<region>.aws.neon.tech/neondb/auth` |
| Frontend, local | `neonAuthUrl` in `src/environments/environment.development.ts` |
| Frontend image | `ROOMRATE_NEON_AUTH_URL` (served as `runtime-config.js`) |
| Neon console | Auth → trusted domains: the app origins, e.g. `http://localhost:4200` |

The URL is in Neon → **Connect** → **Auth**. It is not a secret.

With `NEON_AUTH_URL` unset, the API still verifies Supabase tokens (the
`SUPABASE_*` settings), so an older frontend keeps working during a rollout.

## Moving existing users

Passwords cannot move from Supabase.

1. Create each user in Neon → Auth → Users → **Create user**, with the **same
   email** as before. Alternatively, a user can sign up again.
2. Link their existing RoomRate accounts:

   ```
   python scripts/link_neon_auth_users.py           # dry run: lists what it would do
   python scripts/link_neon_auth_users.py --apply   # links, in one transaction
   ```

   For each matching email, the script does one of three things:
   - **relink**: the user has not logged in since the switch. The old identity
     becomes the Neon one.
   - **move**: the user already logged in and got an empty account. That login
     joins the old account.
   - **skip**: the new account already has a property, or the email is
     ambiguous. Nothing changes.

3. Sign in. The account, property and history are the same as before.

## Known limits

- `@neondatabase/auth` is a beta SDK (pinned to `0.5.0-beta`).
- The session cookie belongs to the Neon Auth domain. Browsers that block
  third-party cookies (e.g. Safari) may not keep the login across reloads until
  the app and its auth live under one site.
- Neon currently lets anyone sign up. A new sign-up gets an empty account, and
  the per-account scrape quotas still apply.
