"""Run only mocked suites with dummy settings; never load live credentials."""
import os
from pathlib import Path
import site
import socket
import sys

ROOT = Path(__file__).resolve().parents[1]
os.chdir(ROOT)

# Priority: project root → system site-packages (Python 3.13-compatible SQLAlchemy)
# → .testdeps (pytest, jose, fastapi, pydantic-settings, httpx, etc.)
# This avoids the SQLAlchemy 2.0.30 / Python 3.13 FastIntFlag incompatibility
# in .testdeps while still using the bundled test-only packages.
for sp in reversed(site.getsitepackages()):
    if sp not in sys.path:
        sys.path.insert(1, sp)
sys.path.insert(0, str(ROOT))
sys.path.append(str(ROOT / ".testdeps"))

os.environ.update(
    SSO_ENABLED="false",
    SSO_JWT_SECRET="test-sso-secret-value-at-least-32-chars!!",
    LENDRIX_SSO_SERVER_SECRET="test-server-secret-value-at-least-32-chars!!",
    LENDRIX_SSO_VERIFY_URL="https://registration.lendrixventech.com/auth/sso/verify-code",
    SSO_COOKIE_NAME="__Host-nse_sso_session",
    SSO_ALLOWED_ORIGINS="https://mutualfund.lendrixventech.com",
    NSE_LOGIN_USER_ID="offline-user",
    NSE_API_SECRET="offline-secret",
    NSE_API_LICENSE_KEY="0123456789abcdef0123456789abcdef",
    NSE_MEMBER_ID="1003338",  # Existing header test's expected fixture value.
    NSE_MEMBER_CODE="offline-member",
    NSE_BASE_URL="https://nse.invalid",
    NSE_PROXY="",
    APP_USERNAME="offline-user",
    APP_PASSWORD="offline-password",
    JWT_SECRET="offline-testing-key-with-at-least-32-characters",
    DB_USER="offline",
    DB_PASSWORD="offline",
    DB_HOST="127.0.0.1",
    DB_PORT="5432",
    DB_NAME="offline",
    DB_SSL="false",
    DATABASE_URL="postgresql+asyncpg://offline:offline@127.0.0.1:5432/offline",
)

# Windows asyncio uses a loopback socket pair internally. Permit that while
# blocking external connections; all application HTTP/database calls are mocked.
_connect = socket.socket.connect
_connect_ex = socket.socket.connect_ex


def _guard(method):
    def guarded(sock, address):
        if isinstance(address, tuple) and address[0] in ("127.0.0.1", "::1"):
            return method(sock, address)
        raise RuntimeError("External network blocked during offline tests")
    return guarded


if __name__ == "__main__":
    socket.socket.connect = _guard(_connect)
    socket.socket.connect_ex = _guard(_connect_ex)
    import pytest

    # Explicit files avoid collecting legacy tests that call external servers.
    sys.exit(pytest.main([
        "test_auth_headers.py", "tests/test_nse_spec_contract.py",
        "tests/test_sso.py", "tests/test_sso_http.py",
        "tests/test_customer_sso.py", "tests/test_customer_api.py",
        "tests/test_nse_auth.py",
        "-q", "--tb=short", "--disable-warnings",
    ]))
