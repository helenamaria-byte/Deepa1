"""Minimal Zoho Inventory API client: OAuth refresh-token flow plus GET/POST.

Credentials come from ZOHO_CLIENT_ID, ZOHO_CLIENT_SECRET, ZOHO_REFRESH_TOKEN,
ZOHO_ORG_ID and ZOHO_DC (data centre: in, com, eu, ...)."""
from __future__ import annotations

import time

import httpx


class ZohoError(RuntimeError):
    pass


class ZohoClient:
    def __init__(self, client_id: str, client_secret: str, refresh_token: str, org_id: str,
                 dc: str = "in", transport: httpx.BaseTransport | None = None):
        self.client_id, self.client_secret, self.refresh_token = client_id, client_secret, refresh_token
        self.org_id = org_id
        self.accounts_url = f"https://accounts.zoho.{dc}/oauth/v2/token"
        self.api_url = f"https://www.zohoapis.{dc}/inventory/v1"
        self.http = httpx.Client(transport=transport, timeout=30)
        self._token, self._expires = "", 0.0

    def _access_token(self) -> str:
        if self._token and time.time() < self._expires - 60:
            return self._token
        resp = self.http.post(self.accounts_url, data={
            "grant_type": "refresh_token", "client_id": self.client_id,
            "client_secret": self.client_secret, "refresh_token": self.refresh_token})
        data = resp.json()
        if "access_token" not in data:
            raise ZohoError(f"Zoho token refresh failed: {data.get('error', resp.status_code)}")
        self._token, self._expires = data["access_token"], time.time() + int(data.get("expires_in", 3600))
        return self._token

    def request(self, method: str, path: str, params: dict | None = None, json: dict | None = None) -> dict:
        params = {"organization_id": self.org_id, **(params or {})}
        resp = self.http.request(method, f"{self.api_url}/{path.lstrip('/')}", params=params, json=json,
                                 headers={"Authorization": f"Zoho-oauthtoken {self._access_token()}"})
        data = resp.json() if resp.content else {}
        if resp.status_code >= 300 or data.get("code", 0) != 0:
            raise ZohoError(f"Zoho {method} {path} failed ({resp.status_code}): {data.get('message', '')}")
        return data

    def get_all(self, path: str, key: str, params: dict | None = None) -> list[dict]:
        """Follow Zoho's page/has_more_page pagination."""
        out, page = [], 1
        while True:
            data = self.request("GET", path, params={**(params or {}), "page": page, "per_page": 200})
            out += data.get(key, [])
            if not data.get("page_context", {}).get("has_more_page"):
                return out
            page += 1
