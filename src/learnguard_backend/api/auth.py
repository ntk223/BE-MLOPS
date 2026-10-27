"""M6 authentication and role authorization contracts."""

from learnguard_backend.db.models import UserRecord


class AuthenticationService:
    def authenticate(self, email: str, password: str) -> UserRecord:
        """TODO: verify credentials and return an active user."""
        raise NotImplementedError

    def create_access_token(self, user: UserRecord) -> str:
        """TODO: issue a signed, expiring token."""
        raise NotImplementedError

    def require_role(self, token: str, allowed_roles: set[str]) -> UserRecord:
        """TODO: validate token and enforce advisor/admin role."""
        raise NotImplementedError
