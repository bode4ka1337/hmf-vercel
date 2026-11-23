# Authentication and administration in HMF Web

## How sign-in works

Opening the site immediately shows a **Sign in** dialog. The application does
not open without signing in. There is no registration in the UI — accounts are
created by the administrator.

### Built-in administrator

```text
Username: admin
Password: nimda
```

This is a normal account (with its own history and settings), but the admin
panel in `Settings → Admin` is available to it. The credentials are defined in
code (`api/auth.py`: `ADMIN_USERNAME` / `ADMIN_PASSWORD`). The
`HMF_ADMIN_PASSWORD` environment variable is no longer used.

The `admin` account is created automatically on first sign-in. It cannot be
deleted or have its password reset, so access to the admin panel cannot be
lost.

## Creating a user account

1. Sign in as `admin` / `nimda`.
2. Open `Settings → Admin`.
3. Enter `New username` and `Password` (at least 8 characters).
4. Click `Add account`.

A new account starts with empty history and settings. Users only see the
sign-in dialog and the regular application; the admin panel is hidden for them.

## Admin panel features

`Settings → Admin` is only available when signed in as `admin`:

- the list of all accounts: username, `user_id`, creation date, and
  history/tag/source counters;
- **Add account** — create a user with a username and password;
- **Reset pwd** — reset a user's password:
  - `OK` — generate a random password, shown once;
  - `Cancel` — enter your own;
- **Delete** — delete the account together with its history and settings;
- **Refresh accounts** — reload the list.

The built-in `admin` account is marked `ADMIN` in the list, and its action
buttons are hidden.

## About passwords

Plaintext passwords are never stored. KV only contains:

- a random salt;
- a `PBKDF2-HMAC-SHA256` hash (200,000 iterations);
- a password version number.

This means a user's current password cannot be viewed. Use **Reset pwd**
instead — the new password is displayed once. A reset increments
`pwd_version`, so all existing sessions of that user become invalid
immediately.

## Technical details

- KV: store `hmf-auth-kv` (Upstash for Redis, free plan, region `fra1`) is
  connected to the `manga_finder` project. `KV_REST_API_URL` and
  `KV_REST_API_TOKEN` were added automatically.
- A session is an `httpOnly` + `Secure` + `SameSite=Lax` cookie named
  `hmf_session`, valid for 30 days.
- On sign-in the server ignores the `user_id` from the request body and uses
  the `user_id` from the session instead.
- Old anonymous history is not migrated into the new account.

### KV keys

- `hmf:accounts` — the username index;
- `hmf:acct:<username>` — account, salt, hash, password version, `is_admin`
  flag;
- `hmf:session:<token>` — a session.

### API actions

User actions: `login`, `logout`, `me`. The `register` action is kept only for
old clients and returns "Registration is disabled".

Admin actions: `admin_status`, `admin_accounts`, `admin_create_account`,
`admin_reset_password`, `admin_delete_account`. All of them require an admin
session.
