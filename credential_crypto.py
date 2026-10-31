import json

from cryptography.fernet import Fernet, InvalidToken


class CredentialError(RuntimeError):
    pass


def encrypt_credentials_token(username, password):
    username = str(username)
    password = str(password)
    if not username or not password:
        raise CredentialError("Mini4K username and password must not be empty")
    key = Fernet.generate_key()
    plaintext = json.dumps(
        {"version": 1, "username": username, "password": password},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    token = Fernet(key).encrypt(plaintext)
    return key.decode("ascii"), token.decode("ascii")


def decrypt_credentials_token(token, key):
    if not token or not key:
        raise CredentialError("Mini4K encrypted credential environment variables are missing")
    try:
        plaintext = Fernet(key.encode("ascii")).decrypt(token.encode("ascii"))
        payload = json.loads(plaintext.decode("utf-8"))
    except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError, ValueError, json.JSONDecodeError) as exc:
        raise CredentialError("Mini4K encrypted credential environment variables are invalid") from exc
    username = payload.get("username") if isinstance(payload, dict) else None
    password = payload.get("password") if isinstance(payload, dict) else None
    if payload.get("version") != 1 or not isinstance(username, str) or not isinstance(password, str):
        raise CredentialError("Mini4K encrypted credential format is invalid")
    if not username or not password:
        raise CredentialError("Mini4K encrypted credentials are empty")
    return username, password

