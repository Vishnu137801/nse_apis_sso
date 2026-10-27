from pydantic_settings import BaseSettings
from pydantic import field_validator, model_validator, Field
from urllib.parse import urlsplit
import re


class Settings(BaseSettings):
    # ── Existing NSE exchange credentials (DO NOT CHANGE) ─────────────────────
    NSE_LOGIN_USER_ID: str = ""
    NSE_API_SECRET: str = ""
    NSE_API_LICENSE_KEY: str = ""
    NSE_MEMBER_CODE: str = ""
    NSE_MEMBER_ID: str = ""
    NSE_BASE_URL: str = "https://nseinvestuat.nseindia.com"
    NSE_PROXY: str = ""

    # ── Existing shared application auth (DO NOT CHANGE) ──────────────────────
    APP_USERNAME: str = ""
    APP_PASSWORD: str = ""
    JWT_SECRET: str
    JWT_EXPIRE_MINUTES: int = 60

    # ── Existing database config (DO NOT CHANGE) ──────────────────────────────
    DB_USER: str
    DB_PASSWORD: str
    DB_HOST: str
    DB_PORT: int = 5432
    DB_NAME: str
    DB_SSL: bool = False

    DATABASE_URL: str

    ALLOWED_ORIGINS: str = ""

    # ── SSO-specific settings (NEW — separate from all existing settings) ──────
    # URL of the Lendrix Node.js one-time code verification endpoint.
    # Example: https://registration.lendrixventech.com/auth/sso/verify-code
    LENDRIX_SSO_VERIFY_URL: str = ""

    # Shared secret sent as Authorization: Bearer <secret> for server-to-server calls.
    # Must be at least 32 characters. Never share with the browser.
    LENDRIX_SSO_SERVER_SECRET: str = ""

    # Separate JWT secret for customer SSO tokens.
    # MUST be different from JWT_SECRET. Never reuse.
    SSO_JWT_SECRET: str = ""

    # Customer SSO session lifetime in minutes.
    SSO_SESSION_EXPIRE_MINUTES: int = Field(default=60, ge=1, le=1440)
    SSO_ENABLED: bool = False
    SSO_COOKIE_NAME: str = "__Host-nse_sso_session"
    SSO_ALLOWED_ORIGINS: str = ""

    @model_validator(mode="after")
    def validate_sso(self):
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self.SSO_COOKIE_NAME):
            raise ValueError("Invalid SSO cookie name")
        origins = [origin.strip() for origin in self.ALLOWED_ORIGINS.split(",") if origin.strip()]
        if "*" in origins:
            raise ValueError("ALLOWED_ORIGINS must not contain wildcard origins")
        if self.SSO_ENABLED:
            url = urlsplit(self.LENDRIX_SSO_VERIFY_URL)
            if url.scheme != "https" or not url.hostname or url.username or url.password or url.query or url.fragment:
                raise ValueError("SSO verification URL must be HTTPS without credentials, query or fragment")
            secrets = [self.JWT_SECRET, self.SSO_JWT_SECRET, self.LENDRIX_SSO_SERVER_SECRET]
            if any(len(value) < 32 for value in secrets) or len(set(secrets)) != 3:
                raise ValueError("SSO requires three distinct secrets of at least 32 characters")
            origins = [o.strip() for o in self.SSO_ALLOWED_ORIGINS.split(",") if o.strip()]
            if not origins:
                raise ValueError("SSO requires explicit allowed origins")
            for origin in origins:
                u = urlsplit(origin)
                if u.scheme != "https" or not u.hostname or u.username or u.password or u.path or u.query or u.fragment:
                    raise ValueError("SSO origins must be exact HTTPS origins")
        return self


    @field_validator("JWT_SECRET")
    @classmethod
    def jwt_secret_must_be_strong(cls, v: str) -> str:
        if not v or v in ("changeme", "secret", "your-super-secret-key-change-this"):
            raise ValueError("JWT_SECRET must be set to a strong random value in .env")
        if len(v) < 32:
            raise ValueError("JWT_SECRET must be at least 32 characters")
        return v

    @field_validator("SSO_JWT_SECRET")
    @classmethod
    def sso_jwt_secret_must_be_strong(cls, v: str) -> str:
        # Only enforce when SSO is actually configured
        if v and len(v) < 32:
            raise ValueError("SSO_JWT_SECRET must be at least 32 characters")
        return v

    class Config:
        env_file = ".env"


settings = Settings()
