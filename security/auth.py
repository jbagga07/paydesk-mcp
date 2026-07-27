from dataclasses import dataclass, field
from typing import Optional
import json
import contextvars
import os
import datetime
import jwt
from contextlib import contextmanager
from dotenv import load_dotenv

# Load environment variables first
load_dotenv()

from db.redisdb import client

# ContextVar to store the authenticated CallerContext for the current request
current_caller_context: contextvars.ContextVar[Optional["CallerContext"]] = contextvars.ContextVar(
    "current_caller_context", default=None
)

JWT_SECRET = os.getenv("JWT_SECRET", "paydesk_jwt_secret_key_2026_safe_and_long_enough")


@dataclass
class CallerContext:
    """
    Represents the authenticated caller.
    """

    caller_type: str
    merchant_id: Optional[str] = None
    user_id: Optional[str] = None
    role: Optional[str] = None
    scopes: list[str] = field(default_factory=list)
    can_view_all_merchants: bool = False

    @property
    def caller_id(self) -> str:
        """
        Returns the appropriate ID for the caller (merchant_id for merchants, user_id for admins).
        """
        if self.caller_type == "merchant":
            return self.merchant_id or "UNKNOWN"
        return self.user_id or "UNKNOWN"


def generate_token(caller_id: str, expires_in_seconds: int = 3600) -> str:
    """
    Generate a JWT token for a given caller_id. Used for testing and client auth.
    """
    payload = {
        "sub": caller_id,
        "exp": datetime.datetime.now(datetime.timezone.utc)
        + datetime.timedelta(seconds=expires_in_seconds),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm="HS256")


def validate_token(token: str) -> str:
    """
    Validate the JWT token and return the sub (caller_id).
    Raises ValueError for expired or malformed/invalid tokens.
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=["HS256"])
        return payload["sub"]
    except jwt.ExpiredSignatureError:
        raise ValueError("Token has expired")
    except jwt.InvalidTokenError:
        raise ValueError("Invalid or malformed token")


def get_current_caller() -> CallerContext:
    """
    Get the current authenticated caller context.
    Raises ValueError if not authenticated.
    """
    context = current_caller_context.get()
    if context is None:
        raise ValueError("Authentication required: No active caller context.")
    return context


@contextmanager
def authenticated_as(caller_id: str):
    """
    Context manager to temporarily authenticate as a caller. Useful for testing.
    """
    context = resolve_caller(caller_id)
    token = current_caller_context.set(context)
    try:
        yield context
    finally:
        current_caller_context.reset(token)


def resolve_caller(caller_id: str) -> CallerContext:
    """
    Resolve a merchant or admin from Redis.
    """

    # Merchant
    merchant_key = f"merchant:{caller_id}"

    if client.exists(merchant_key):
        return CallerContext(
            caller_type="merchant",
            merchant_id=caller_id,
            role="MERCHANT"
        )

    # Admin
    admin_key = f"admin:{caller_id}"

    if client.exists(admin_key):
        admin_data = client.hgetall(admin_key)

        scopes = json.loads(admin_data.get("scopes", "[]"))

        return CallerContext(
            caller_type="admin",
            user_id=admin_data.get("user_id"),
            role=admin_data.get("role"),
            scopes=scopes,
            can_view_all_merchants=(
                admin_data.get("can_view_all_merchants", "False").lower() == "true"
            )
        )

    raise ValueError(f"Unknown caller: {caller_id}")


def get_or_create_ssl_certs(cert_dir: str = "security/certs") -> tuple[str, str]:
    """
    Check if TLS key and certificate exist; if not, generate self-signed certificates.
    """
    os.makedirs(cert_dir, exist_ok=True)
    key_path = os.path.join(cert_dir, "server.key")
    cert_path = os.path.join(cert_dir, "server.crt")

    if os.path.exists(key_path) and os.path.exists(cert_path):
        return key_path, cert_path

    # Generate RSA private key
    from cryptography.hazmat.primitives.asymmetric import rsa
    private_key = rsa.generate_private_key(
        public_exponent=65537,
        key_size=2048,
    )

    # Generate self-signed certificate
    import ipaddress
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives import serialization

    subject = issuer = x509.Name([
        x509.NameAttribute(NameOID.COUNTRY_NAME, "US"),
        x509.NameAttribute(NameOID.STATE_OR_PROVINCE_NAME, "California"),
        x509.NameAttribute(NameOID.LOCALITY_NAME, "San Francisco"),
        x509.NameAttribute(NameOID.ORGANIZATION_NAME, "PayDesk"),
        x509.NameAttribute(NameOID.COMMON_NAME, "localhost"),
    ])

    cert = x509.CertificateBuilder().subject_name(
        subject
    ).issuer_name(
        issuer
    ).public_key(
        private_key.public_key()
    ).serial_number(
        x509.random_serial_number()
    ).not_valid_before(
        datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=1)
    ).not_valid_after(
        # 1 year validity
        datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=365)
    ).add_extension(
        x509.SubjectAlternativeName([
            x509.DNSName("localhost"),
            x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
        ]),
        critical=False,
    ).sign(private_key, hashes.SHA256())

    # Save private key
    with open(key_path, "wb") as f:
        f.write(
            private_key.private_bytes(
                encoding=serialization.Encoding.PEM,
                format=serialization.PrivateFormat.TraditionalOpenSSL,
                encryption_algorithm=serialization.NoEncryption(),
            )
        )

    # Save certificate
    with open(cert_path, "wb") as f:
        f.write(
            cert.public_bytes(serialization.Encoding.PEM)
        )

    return key_path, cert_path


from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class BearerAuthMiddleware(BaseHTTPMiddleware):
    """
    Middleware that reads, decodes, and validates JWT Bearer tokens from the Authorization header.
    Resolves the authenticated CallerContext and binds it to current_caller_context contextvar.
    """
    async def dispatch(self, request: Request, call_next):
        if request.url.path == "/mcp":
            auth_header = request.headers.get("Authorization")
            if not auth_header:
                return JSONResponse({"error": "Missing Authorization header"}, status_code=401)

            if not auth_header.startswith("Bearer "):
                return JSONResponse({"error": "Malformed Authorization header"}, status_code=401)

            token = auth_header[len("Bearer "):]
            try:
                caller_id = validate_token(token)
                context = resolve_caller(caller_id)
            except Exception as e:
                return JSONResponse({"error": f"Invalid token: {str(e)}"}, status_code=401)

            token_token = current_caller_context.set(context)
            try:
                response = await call_next(request)
                return response
            finally:
                current_caller_context.reset(token_token)
        else:
            return await call_next(request)

