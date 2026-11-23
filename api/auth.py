"""Username/password accounts and cookie sessions for the HMF web client.

Accounts and sessions live in Vercel KV REST (the same env vars as ``state.py``):
``KV_REST_API_URL`` and ``KV_REST_API_TOKEN``. Without them every auth action
returns a clear "not configured" error.

There is no public registration. Accounts are issued by the built-in ``admin``
account, which behaves like a normal account but has the admin panel enabled.

Keys:
  hmf:acct:<casefolded username> -> account record with PBKDF2 hash
  hmf:session:<random token>     -> session record with expiry
  hmf:accounts                   -> index of usernames
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import time
from typing import Any
from urllib.parse import quote

AUTH_UNAVAILABLE = "Authentication is not configured on the server (KV env vars are missing)."
ADMIN_REQUIRED = "Admin authentication required."
REGISTRATION_DISABLED = "Registration is disabled. Ask the administrator for an account."
ADMIN_PROTECTED = "The built-in admin account cannot be changed."
SESSION_COOKIE = "hmf_session"
SESSION_TTL = 60 * 60 * 24 * 30  # 30 days

ADMIN_USERNAME = "admin"
ADMIN_PASSWORD = "nimda"

ACCOUNT_PREFIX = "hmf:acct:"
SESSION_PREFIX = "hmf:session:"
ACCOUNT_INDEX_KEY = "hmf:accounts"
_USERNAME_RE = re.compile(r"[\w.-]{3,32}", re.UNICODE)
_PBKDF2_ROUNDS = 200_000


def configured() -> bool:
    return bool(
        os.environ.get("KV_REST_API_URL") and os.environ.get("KV_REST_API_TOKEN")
    )


def _kv_request(method: str, suffix: str, *, data: bytes | None = None) -> Any:
    from core.http import request as http_request

    base = str(os.environ.get("KV_REST_API_URL") or "").strip().rstrip("/")
    token = str(os.environ.get("KV_REST_API_TOKEN") or "").strip()
    headers = {"Authorization": f"Bearer {token}", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    response = http_request(
        method,
        f"{base}/{suffix}",
        headers=headers,
        data=data,
        retries=1,
        timeout=3.0,
    )
    try:
        return response.json()
    except Exception:
        return None


def _kv_get(key: str) -> Any:
    try:
        payload = _kv_request("GET", f"get/{quote(key, safe='')}")
    except Exception:
        return None
    if not isinstance(payload, dict) or payload.get("error"):
        return None
    result = payload.get("result")
    if isinstance(result, str):
        try:
            result = json.loads(result)
        except (TypeError, ValueError):
            return None
    return result if isinstance(result, dict) else None


def _kv_set(key: str, value: dict[str, Any]) -> bool:
    data = json.dumps(value, ensure_ascii=False).encode("utf-8")
    try:
        payload = _kv_request("POST", f"set/{quote(key, safe='')}", data=data)
    except Exception:
        return False
    return isinstance(payload, dict) and not payload.get("error")


def _hash_password(password: str, salt: bytes) -> str:
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ROUNDS
    ).hex()


def _account_key(username: str) -> str:
    return ACCOUNT_PREFIX + username.casefold()


def _session_key(token: str) -> str:
    return SESSION_PREFIX + token


def _index_load() -> list[str]:
    data = _kv_get(ACCOUNT_INDEX_KEY)
    names = data.get("names") if isinstance(data, dict) else None
    if not isinstance(names, list):
        return []
    return [str(name) for name in names if str(name)]


def _index_save(names: list[str]) -> None:
    _kv_set(ACCOUNT_INDEX_KEY, {"names": names})


def _index_add(username_key: str) -> None:
    names = _index_load()
    if username_key not in names:
        names.append(username_key)
        _index_save(names)


def _index_remove(username_key: str) -> None:
    names = _index_load()
    if username_key in names:
        _index_save([name for name in names if name != username_key])


def _start_session(username: str, user_id: str, pwd_version: int = 1) -> str:
    token = secrets.token_urlsafe(32)
    session = {
        "username": username,
        "user_id": user_id,
        "pwd_version": pwd_version,
        "created_at": time.time(),
        "expires_at": time.time() + SESSION_TTL,
    }
    _kv_set(_session_key(token), session)
    return token


def register(body: dict[str, Any]) -> dict[str, Any]:
    """Kept only to give old clients a clear message. Admin creates accounts."""
    return {"error": REGISTRATION_DISABLED}


def _create_account(
    username: str, password: str, *, is_admin: bool = False
) -> dict[str, Any]:
    if not configured():
        return {"error": AUTH_UNAVAILABLE}
    username = str(username).strip()
    password = str(password)

    if not _USERNAME_RE.fullmatch(username):
        return {
            "error": "Username must be 3-32 letters, numbers, dots, dashes or underscores."
        }
    # The built-in admin password is deliberately short; user accounts are not.
    if len(password) < 8 and not is_admin:
        return {"error": "Password must be at least 8 characters."}

    key = _account_key(username)
    existing = _kv_get(key)
    if existing is not None and not existing.get("deleted"):
        return {"error": "That username is already taken."}

    salt = secrets.token_bytes(16)
    account = {
        "username": username,
        "user_id": "u_" + secrets.token_hex(12),
        "salt": salt.hex(),
        "password_hash": _hash_password(password, salt),
        "pwd_version": 1,
        "created_at": time.time(),
        "is_admin": bool(is_admin),
    }
    if not _kv_set(key, account):
        return {"error": "Could not create the account. Try again."}
    _index_add(username.casefold())
    return {
        "ok": True,
        "username": account["username"],
        "user_id": account["user_id"],
    }


def ensure_admin_account() -> bool:
    """Create the built-in admin account on first use (idempotent)."""
    if not configured():
        return False
    key = _account_key(ADMIN_USERNAME)
    account = _kv_get(key)
    if account and not account.get("deleted") and account.get("is_admin"):
        return True
    result = _create_account(ADMIN_USERNAME, ADMIN_PASSWORD, is_admin=True)
    if result.get("ok"):
        return True
    # An old admin record without the flag can still be repaired in place.
    if account and not account.get("deleted"):
        account["is_admin"] = True
        return _kv_set(key, account)
    return False


def login(body: dict[str, Any]) -> dict[str, Any]:
    if not configured():
        return {"error": AUTH_UNAVAILABLE}
    ensure_admin_account()

    username = str(body.get("username") or "").strip()
    password = str(body.get("password") or "")
    account = _kv_get(_account_key(username)) if username else None
    if not account or account.get("deleted"):
        return {"error": "Invalid username or password."}

    try:
        salt = bytes.fromhex(str(account.get("salt") or ""))
        expected = str(account.get("password_hash") or "")
    except (TypeError, ValueError):
        return {"error": "Invalid username or password."}
    if not salt or not expected:
        return {"error": "Invalid username or password."}
    if not hmac.compare_digest(_hash_password(password, salt), expected):
        return {"error": "Invalid username or password."}

    token = _start_session(
        str(account["username"]),
        str(account["user_id"]),
        int(account.get("pwd_version") or 1),
    )
    return {
        "ok": True,
        "username": account["username"],
        "user_id": account["user_id"],
        "is_admin": bool(account.get("is_admin")),
        "token": token,
    }


def session_user(token: str | None) -> dict[str, Any] | None:
    if not token or not configured():
        return None
    session = _kv_get(_session_key(token))
    if not session:
        return None
    try:
        expires_at = float(session.get("expires_at") or 0)
    except (TypeError, ValueError):
        return None
    if expires_at <= time.time():
        return None
    username = str(session.get("username") or "")
    if not username:
        return None
    account = _kv_get(_account_key(username))
    if not account or account.get("deleted"):
        return None
    if str(account.get("pwd_version") or 1) != str(session.get("pwd_version") or 1):
        return None
    user_id = str(account.get("user_id") or "")
    if not user_id:
        return None
    return {
        "username": username,
        "user_id": user_id,
        "is_admin": bool(account.get("is_admin")),
    }


def me(token: str | None) -> dict[str, Any]:
    user = session_user(token)
    if user:
        return {"authenticated": True, **user}
    result: dict[str, Any] = {"authenticated": False}
    if not configured():
        result["error"] = AUTH_UNAVAILABLE
    return result


def logout(token: str | None) -> dict[str, Any]:
    if token and configured():
        _kv_set(_session_key(token), {"revoked": True, "expires_at": 0})
    return {"ok": True, "authenticated": False}


# ------------------------------------------------------------------- admin


def admin_status(token: str | None) -> dict[str, Any]:
    user = session_user(token)
    is_admin = bool(user and user.get("is_admin"))
    return {
        "configured": configured(),
        "authenticated": is_admin,
        "is_admin": is_admin,
    }


def _admin_guard(token: str | None) -> str | None:
    if not configured():
        return AUTH_UNAVAILABLE
    user = session_user(token)
    if not user or not user.get("is_admin"):
        return ADMIN_REQUIRED
    return None


def admin_create_account(token: str | None, body: dict[str, Any]) -> dict[str, Any]:
    error = _admin_guard(token)
    if error:
        return {"error": error}
    username = str(body.get("username") or "").strip()
    if username.casefold() == ADMIN_USERNAME.casefold():
        return {"error": ADMIN_PROTECTED}
    result = _create_account(username, str(body.get("password") or ""))
    if result.get("ok"):
        return {"ok": True, "username": result["username"], "user_id": result["user_id"]}
    return result


def _state_module():
    try:
        from .state import get_user, reset_user
    except ImportError:  # pragma: no cover - flat imports
        from state import get_user, reset_user  # type: ignore[no-redef]
    return get_user, reset_user


def admin_accounts(token: str | None) -> dict[str, Any]:
    error = _admin_guard(token)
    if error:
        return {"error": error}
    get_user, _ = _state_module()

    accounts: list[dict[str, Any]] = []
    for name in _index_load():
        account = _kv_get(_account_key(name))
        if not account or account.get("deleted"):
            continue
        user_id = str(account.get("user_id") or "")
        history_count = 0
        tags_count = 0
        sources_count = 0
        if user_id:
            try:
                state = get_user(user_id)
                history_count = len(state.get("history") or [])
                settings = state.get("settings") or {}
                tags_count = len(settings.get("tags") or [])
                sources_count = len(settings.get("sources") or [])
            except Exception:
                pass
        accounts.append(
            {
                "username": account.get("username") or name,
                "user_id": user_id,
                "is_admin": bool(account.get("is_admin")),
                "created_at": float(account.get("created_at") or 0),
                "pwd_version": int(account.get("pwd_version") or 1),
                "history_count": history_count,
                "tags_count": tags_count,
                "sources_count": sources_count,
            }
        )
    accounts.sort(key=lambda item: item["created_at"], reverse=True)
    return {"ok": True, "accounts": accounts}


def admin_reset_password(token: str | None, body: dict[str, Any]) -> dict[str, Any]:
    error = _admin_guard(token)
    if error:
        return {"error": error}
    username = str(body.get("username") or "").strip()
    if username.casefold() == ADMIN_USERNAME.casefold():
        return {"error": ADMIN_PROTECTED}
    key = _account_key(username) if username else ""
    account = _kv_get(key) if key else None
    if not account or account.get("deleted"):
        return {"error": "Account not found."}

    new_password = str(body.get("new_password") or "")
    generated = False
    if not new_password:
        new_password = secrets.token_urlsafe(9)
        generated = True
    if len(new_password) < 8:
        return {"error": "Password must be at least 8 characters."}

    salt = secrets.token_bytes(16)
    account["salt"] = salt.hex()
    account["password_hash"] = _hash_password(new_password, salt)
    account["pwd_version"] = int(account.get("pwd_version") or 1) + 1
    if not _kv_set(key, account):
        return {"error": "Could not update the password."}

    return {
        "ok": True,
        "username": account.get("username") or username,
        "new_password": new_password,
        "generated": generated,
    }


def admin_delete_account(token: str | None, body: dict[str, Any]) -> dict[str, Any]:
    error = _admin_guard(token)
    if error:
        return {"error": error}
    username = str(body.get("username") or "").strip()
    if username.casefold() == ADMIN_USERNAME.casefold():
        return {"error": ADMIN_PROTECTED}
    key = _account_key(username) if username else ""
    account = _kv_get(key) if key else None
    if not account or account.get("deleted"):
        return {"error": "Account not found."}

    user_id = str(account.get("user_id") or "")
    account["deleted"] = True
    account["deleted_at"] = time.time()
    account["pwd_version"] = int(account.get("pwd_version") or 1) + 1
    _kv_set(key, account)
    _index_remove(username.casefold())

    _, reset_user = _state_module()
    if user_id:
        try:
            reset_user(user_id, "all")
        except Exception:
            pass
    return {"ok": True, "username": account.get("username") or username}
