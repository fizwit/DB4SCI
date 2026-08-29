import base64
import hashlib
import hmac
import os

""" create SCRAM-SHA-256 hashed password for creating User account """


def _hi(password: str, salt: bytes, iterations: int) -> bytes:
    # PBKDF2-HMAC-SHA-256, matches SCRAM RFC 5802 / PostgreSQL verifier generation
    return hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)

def _xor_bytes(a: bytes, b: bytes) -> bytes:
    return bytes(x ^ y for x, y in zip(a, b))

def _hmac_sha256(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()

def _b64_no_ws(data: bytes) -> str:
    # PostgreSQL verifier uses standard base64 (with '=' padding)
    return base64.b64encode(data).decode("ascii")

def postgres_hash(password: str, rounds: int = 4096) -> str:
    """
    Returns a PostgreSQL stored verifier string for scram-sha-256:
      SCRAM-SHA-256$<rounds>:<salt_b64>$<stored_key_b64>:<server_key_b64>
      rounds = 4096   # PosgreSQL 17 and 18
    """
    salt = os.urandom(16)
    salted_password = _hi(password, salt, rounds)  # SaltedPassword

    client_key = _hmac_sha256(salted_password, b"Client Key")
    stored_key = hashlib.sha256(client_key).digest()

    server_key = _hmac_sha256(salted_password, b"Server Key")

    salt_b64 = _b64_no_ws(salt)
    stored_key_b64 = _b64_no_ws(stored_key)
    server_key_b64 = _b64_no_ws(server_key)

    return f"SCRAM-SHA-256${rounds}:{salt_b64}${stored_key_b64}:{server_key_b64}"


if __name__ == "__main__":
    user_name = 'jfdey'
    password = "jfdeytest"
    salt = os.urandom(16)

    v = postgres_hash(password)
    create_str = f"CREATE ROLE {user_name}  WITH LOGIN PASSWORD '{v}';"
    print(create_str)
