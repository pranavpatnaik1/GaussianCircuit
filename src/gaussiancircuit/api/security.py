import hashlib
import hmac
import secrets

from fastapi import Header, HTTPException, status

ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def new_pairing_code() -> str:
    left = "".join(secrets.choice(ALPHABET) for _ in range(3))
    right = "".join(secrets.choice(ALPHABET) for _ in range(3))
    return f"{left}-{right}"


def normalize_code(value: str) -> str:
    raw = "".join(character for character in value.upper() if character in ALPHABET)
    if len(raw) != 6:
        raise HTTPException(status_code=422, detail="Capture code must contain six characters")
    return f"{raw[:3]}-{raw[3:]}"


def new_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def token_matches(token: str, expected_hash: str | None) -> bool:
    return expected_hash is not None and hmac.compare_digest(token_hash(token), expected_hash)


def bearer_token(authorization: str | None = Header(default=None)) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    token = authorization.removeprefix("Bearer ").strip()
    if not token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Bearer token required")
    return token
