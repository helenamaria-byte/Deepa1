"""Creates the database tables and dashboard logins.

    python -m src.jobs.bootstrap

DASHBOARD_USERS (email:password pairs) is used if set. Otherwise one login each
is generated for Raghav, procurement and Deepa; only password hashes are stored
and the plain passwords are delivered once by email (or to the outbox)."""
from __future__ import annotations

import secrets

from src.outputs.email import Mailer, render
from src.settings import settings
from src.store.repo import Store

DEFAULT_USERS = [("raghav", "Raghav (planning)"), ("procurement", "Procurement team"), ("deepa", "Deepa")]


def bootstrap(store: Store | None = None, mailer: Mailer | None = None, force: bool = False) -> list[str]:
    store = store or Store()
    store.migrate()
    if store.usernames() and not force:
        return store.usernames()
    if settings.dashboard_users:
        for pair in settings.dashboard_users.split(","):
            login, _, password = pair.strip().partition(":")
            if login and password:
                store.create_user(login.strip().lower(), password.strip(), email=login.strip().lower())
        return store.usernames()

    users = []
    for username, label in DEFAULT_USERS:
        password = secrets.token_urlsafe(9)
        email = (settings.recipients[username] or [""])[0]
        store.create_user(username, password, email=email)
        users.append({"username": username, "label": label, "password": password, "email": email})
    mailer = mailer or Mailer(store)
    html = render("credentials.html", users=users)
    to = sorted({u["email"] for u in users if u["email"]})
    mailer.send("credentials", to, "Your Freshroots planning dashboard logins", html, keep_body=False)
    return store.usernames()


if __name__ == "__main__":
    print("Users:", ", ".join(bootstrap()))
