"""
SSO mapping model.

Maps a verified Lendrix UUID to one or more NSE client codes.
This table is the ONLY authoritative source for customer-to-investor authorization.
It must be populated manually or via a verified admin process — never auto-linked.
"""
from sqlalchemy import Column, Integer, String, Boolean, DateTime, UniqueConstraint, Index, JSON, text
from sqlalchemy.sql import func
from database import Base


class LendrixUserNseMapping(Base):
    __tablename__ = "lendrix_user_nse_mapping"

    __table_args__ = (
        UniqueConstraint("lendrix_uuid", "client_code", name="uq_lendrix_uuid_client_code"),
        Index("ix_lendrix_uuid_nse_mapping", "lendrix_uuid"),
        Index("ix_client_code_nse_mapping", "client_code"),
        Index(
            "uq_active_nse_client_code",
            "client_code",
            unique=True,
            postgresql_where=text("is_active = TRUE"),
        ),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)

    # Verified Lendrix user UUID (UUID string from Node.js cusers table).
    # Not a FK — this DB does not have access to the Lendrix PG instance.
    lendrix_uuid = Column(String(36), nullable=False)

    # NSE investor client code (10-char, assigned by NSE during UCC registration).
    client_code = Column(String(10), nullable=False)

    # Whether this mapping is currently active. Soft-disable without deleting history.
    is_active = Column(Boolean, nullable=False, default=True)

    verification_method = Column(String(40))
    verification_reference = Column(String(36))
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class CustomerUccAttempt(Base):
    __tablename__ = "customer_ucc_attempts"

    __table_args__ = (
        UniqueConstraint(
            "lendrix_uuid", "idempotency_key",
            name="uq_customer_ucc_attempt_idempotency",
        ),
        Index("ix_customer_ucc_attempt_uuid_created", "lendrix_uuid", "created_at"),
    )

    id = Column(String(36), primary_key=True)
    lendrix_uuid = Column(String(36), nullable=False, index=True)
    idempotency_key = Column(String(36), nullable=False)
    request_fingerprint = Column(String(64), nullable=False)
    status = Column(String(40), nullable=False)
    nse_statuses = Column(JSON)
    client_codes = Column(JSON)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


class SsoSessionRevocation(Base):
    """
    Revoked SSO session JTIs.
    Allows logout and forced session invalidation without waiting for token expiry.
    Rows older than SSO_SESSION_EXPIRE_MINUTES can be pruned safely.
    """
    __tablename__ = "sso_session_revocations"

    __table_args__ = (
        Index("ix_sso_jti", "jti"),
    )

    id = Column(Integer, primary_key=True, autoincrement=True)
    jti = Column(String(36), nullable=False, unique=True)
    lendrix_uuid = Column(String(36), nullable=False)
    revoked_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)


class NseSsoSession(Base):
    """Persistent customer SSO session metadata; never stores the signed JWT."""

    __tablename__ = "nse_sso_sessions"

    __table_args__ = (
        UniqueConstraint("jwt_jti_hash", name="uq_nse_sso_sessions_jti_hash"),
        Index("ix_nse_sso_sessions_customer", "lendrix_user_uuid"),
        Index("ix_nse_sso_sessions_expires_at", "expires_at"),
    )

    id = Column(String(36), primary_key=True)
    lendrix_user_uuid = Column(String(36), nullable=False)
    jwt_jti_hash = Column(String(64), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False)
    revoked_at = Column(DateTime(timezone=True))
    last_used_at = Column(DateTime(timezone=True))
