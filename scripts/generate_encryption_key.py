"""Print one newly generated Fernet key without modifying configuration files."""

from cryptography.fernet import Fernet


def main() -> None:
    """Generate and print a URL-safe base64 encoded 32-byte Fernet key."""
    print(Fernet.generate_key().decode("ascii"))


if __name__ == "__main__":
    main()
