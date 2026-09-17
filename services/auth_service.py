"""Password authentication and revocable database session tokens."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from database import User, UserSession


PASSWORD_ITERATIONS = 600_000
SESSION_DAYS = 30


class EmailAlreadyRegistered(ValueError):
    pass


class InvalidCredentials(ValueError):
    pass


def normalize_email(email: str) -> str:
    return email.strip().casefold()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )
    return "$".join(
        (
            "pbkdf2_sha256",
            str(PASSWORD_ITERATIONS),
            base64.urlsafe_b64encode(salt).decode("ascii"),
            base64.urlsafe_b64encode(digest).decode("ascii"),
        )
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, iterations, salt_text, expected_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode("ascii"))
        expected = base64.urlsafe_b64decode(expected_text.encode("ascii"))
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _as_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class AuthService:
    def __init__(self, session: Session):
        self.session = session

    def register(self, email: str, password: str, display_name: str) -> tuple[User, str]:
        normalized = normalize_email(email)
        existing = self.session.scalar(select(User).where(User.email == normalized))
        if existing:
            raise EmailAlreadyRegistered("An account with this email already exists.")

        user = User(
            email=normalized,
            password_hash=hash_password(password),
            display_name=display_name.strip(),
        )
        self.session.add(user)
        try:
            self.session.flush()
        except IntegrityError as exc:
            self.session.rollback()
            raise EmailAlreadyRegistered(
                "An account with this email already exists."
            ) from exc
        return user, self.create_session(user)

    def login(self, email: str, password: str) -> tuple[User, str]:
        user = self.session.scalar(
            select(User).where(User.email == normalize_email(email))
        )
        if user is None or not verify_password(password, user.password_hash):
            raise InvalidCredentials("Email or password is incorrect.")
        return user, self.create_session(user)

    def create_session(self, user: User) -> str:
        token = secrets.token_urlsafe(32)
        self.session.add(
            UserSession(
                user=user,
                token_hash=_token_hash(token),
                expires_at=datetime.now(timezone.utc) + timedelta(days=SESSION_DAYS),
            )
        )
        self.session.flush()
        return token

    def authenticate(self, token: str | None) -> User | None:
        if not token:
            return None
        stored = self.session.scalar(
            select(UserSession).where(UserSession.token_hash == _token_hash(token))
        )
        if (
            stored is None
            or stored.revoked_at is not None
            or _as_utc(stored.expires_at) <= datetime.now(timezone.utc)
        ):
            return None
        return stored.user

    def logout(self, token: str | None) -> None:
        if not token:
            return
        stored = self.session.scalar(
            select(UserSession).where(UserSession.token_hash == _token_hash(token))
        )
        if stored and stored.revoked_at is None:
            stored.revoked_at = datetime.now(timezone.utc)
