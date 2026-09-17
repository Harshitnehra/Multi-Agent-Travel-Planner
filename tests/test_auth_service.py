import unittest
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from database import Base, UserSession, build_engine, session_scope
from services.auth_service import AuthService, InvalidCredentials, verify_password


class AuthServiceTests(unittest.TestCase):
    def setUp(self):
        self.engine = build_engine("sqlite://", shared_memory=True)
        Base.metadata.create_all(self.engine)

    def tearDown(self):
        self.engine.dispose()

    def test_password_is_hashed_and_session_can_be_revoked(self):
        with session_scope(self.engine) as session:
            user, token = AuthService(session).register(
                "User@Example.com", "a-long-test-password", "Test User"
            )
            user_id = user.id
            self.assertNotIn("a-long-test-password", user.password_hash)
            self.assertTrue(verify_password("a-long-test-password", user.password_hash))

        with session_scope(self.engine) as session:
            service = AuthService(session)
            self.assertEqual(service.authenticate(token).id, user_id)
            service.logout(token)

        with session_scope(self.engine) as session:
            self.assertIsNone(AuthService(session).authenticate(token))

    def test_expired_session_is_rejected(self):
        with session_scope(self.engine) as session:
            user, token = AuthService(session).register(
                "user@example.com", "a-long-test-password", "Test User"
            )
            stored = session.scalar(select(UserSession).where(UserSession.user_id == user.id))
            stored.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)

        with session_scope(self.engine) as session:
            self.assertIsNone(AuthService(session).authenticate(token))

    def test_wrong_password_is_rejected(self):
        with session_scope(self.engine) as session:
            service = AuthService(session)
            service.register("user@example.com", "a-long-test-password", "Test User")
            with self.assertRaises(InvalidCredentials):
                service.login("user@example.com", "wrong-password")


if __name__ == "__main__":
    unittest.main()
