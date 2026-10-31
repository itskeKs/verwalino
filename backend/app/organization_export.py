"""Remove expired product exports, without opening the database or originals."""
import argparse

from app.services.organization_export_service import cleanup_expired_exports


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["cleanup"])
    parser.parse_args()
    print(f"Expired exports removed: {cleanup_expired_exports()}")


if __name__ == "__main__":
    main()
