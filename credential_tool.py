import getpass
from credential_crypto import CredentialError, encrypt_credentials_token


def main():
    print("Mini4K encrypted credential setup")
    username = input("Username or email: ").strip()
    password = getpass.getpass("Password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match; nothing was written.")
    try:
        key, token = encrypt_credentials_token(username, password)
    except CredentialError as exc:
        raise SystemExit(str(exc)) from exc
    print("\nAdd these environment variables to the Mini4K bridge:")
    print(f"MINI4K_CREDENTIALS_KEY={key}")
    print(f"MINI4K_CREDENTIALS_TOKEN={token}")
    print("\nTreat both values as secrets. The original password was not written to disk.")


if __name__ == "__main__":
    main()

