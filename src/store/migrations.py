"""Schema migrations. Version 1 creates every table; later versions add steps here.

Run directly with:  python -m src.store.migrations
"""
from src.store.repo import Store


def main() -> None:
    Store().migrate()
    print("Database schema is up to date.")


if __name__ == "__main__":
    main()
