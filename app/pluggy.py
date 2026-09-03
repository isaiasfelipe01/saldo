"""Pluggy API client and idempotent Supabase synchronization helpers.

The client credentials never leave the backend.  Pluggy is the upstream source
of truth, while Supabase is the application's read model (and keeps user
category/description edits).
"""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from typing import Any, Iterable, Optional
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx

from app.database import supabase_client


PLUGGY_API_URL = "https://api.pluggy.ai"


class PluggyError(Exception):
    def __init__(self, message: str, status_code: int = 502, code: Optional[str] = None):
        super().__init__(message)
        self.status_code = status_code
        self.code = code


class PluggyClient:
    """Small synchronous client with an in-memory, proactively refreshed API key."""

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self._api_key: Optional[str] = None
        self._expires_at = datetime.min.replace(tzinfo=timezone.utc)
        self._lock = threading.Lock()

    def _request(
        self,
        method: str,
        path_or_url: str,
        *,
        params: Optional[dict[str, Any]] = None,
        json: Optional[dict[str, Any]] = None,
    ) -> Any:
        api_key = self._get_api_key()
        url = path_or_url if path_or_url.startswith("https://") else f"{PLUGGY_API_URL}{path_or_url}"
        parsed = urlparse(url)
        if parsed.scheme != "https" or parsed.netloc != "api.pluggy.ai":
            raise PluggyError("A Pluggy retornou um endereço de sincronização inválido.")

        try:
            response = httpx.request(
                method,
                url,
                params=params,
                json=json,
                headers={"X-API-KEY": api_key, "Accept": "application/json"},
                timeout=30.0,
            )
        except httpx.HTTPError as exc:
            raise PluggyError("Não foi possível acessar a Pluggy agora.") from exc

        if response.is_error:
            try:
                payload = response.json()
            except ValueError:
                payload = {}
            message = payload.get("message") or payload.get("codeDescription") or "Erro retornado pela Pluggy."
            raise PluggyError(message, response.status_code, payload.get("codeDescription"))

        if response.status_code == 204 or not response.content:
            return None
        return response.json()

    def _get_api_key(self) -> str:
        if self._api_key and datetime.now(timezone.utc) < self._expires_at:
            return self._api_key

        with self._lock:
            if self._api_key and datetime.now(timezone.utc) < self._expires_at:
                return self._api_key
            try:
                response = httpx.post(
                    f"{PLUGGY_API_URL}/auth",
                    json={"clientId": self.client_id, "clientSecret": self.client_secret},
                    timeout=20.0,
                )
                response.raise_for_status()
                self._api_key = response.json()["apiKey"]
                # API keys last two hours; renew five minutes early.
                self._expires_at = datetime.now(timezone.utc) + timedelta(minutes=115)
                return self._api_key
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                raise PluggyError("Não foi possível autenticar a aplicação na Pluggy.") from exc

    def create_connect_token(
        self,
        user_id: str,
        webhook_url: str,
        item_id: Optional[str] = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "options": {
                "clientUserId": user_id,
                "webhookUrl": webhook_url,
                "avoidDuplicates": True,
            }
        }
        if item_id:
            payload["itemId"] = item_id
        return self._request("POST", "/connect_token", json=payload)

    def get_item(self, item_id: str) -> dict[str, Any]:
        return self._request("GET", f"/items/{item_id}")

    def update_item(self, item_id: str) -> dict[str, Any]:
        return self._request("PATCH", f"/items/{item_id}", json={})

    def delete_item(self, item_id: str) -> None:
        self._request("DELETE", f"/items/{item_id}")

    def get_accounts(self, item_id: str) -> list[dict[str, Any]]:
        payload = self._request("GET", "/accounts", params={"itemId": item_id})
        return payload.get("results", payload if isinstance(payload, list) else [])

    def get_transaction_page(
        self,
        account_id: str,
        *,
        transaction_ids: Optional[Iterable[str]] = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"accountId": account_id}
        ids = list(transaction_ids or [])
        if ids:
            params["ids"] = ",".join(ids)
        return self._request("GET", "/v2/transactions", params=params)

    def get_all_transactions(self, account_id: str) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        payload = self.get_transaction_page(account_id)
        while True:
            page_results = payload.get("results", [])
            results.extend(page_results)
            next_page = payload.get("next")
            if not next_page:
                break
            if not isinstance(next_page, str) or not next_page.startswith("?"):
                raise PluggyError("A Pluggy retornou um cursor de paginação inválido.")
            payload = self._request("GET", f"/v2/transactions{next_page}")
        return results

    def get_transactions_from_link(self, url: str) -> list[dict[str, Any]]:
        payload = self._request("GET", url)
        results = list(payload.get("results", []))
        parsed = urlparse(url)
        while payload.get("next"):
            next_page = payload["next"]
            if not isinstance(next_page, str) or not next_page.startswith("?"):
                raise PluggyError("A Pluggy retornou um cursor de paginação inválido.")
            next_url = urlunparse(
                (parsed.scheme, parsed.netloc, parsed.path, "", next_page[1:], "")
            )
            payload = self._request("GET", next_url)
            results.extend(payload.get("results", []))

        # Compatibility with the legacy createdTransactionsLink for
        # applications created before the V2 cursor endpoint.
        total = payload.get("total")
        page = 1
        while isinstance(total, int) and len(results) < total:
            page += 1
            query = dict(parse_qsl(parsed.query, keep_blank_values=True))
            query.update({"page": str(page), "pageSize": "500"})
            page_url = urlunparse(
                (parsed.scheme, parsed.netloc, parsed.path, "", urlencode(query), "")
            )
            payload = self._request("GET", page_url)
            page_results = payload.get("results", [])
            if not page_results:
                break
            results.extend(page_results)
        return results


CATEGORY_MAP = {
    "food": "Alimentação",
    "restaurants": "Alimentação",
    "groceries": "Supermercado",
    "supermarket": "Supermercado",
    "transportation": "Transporte",
    "transport": "Transporte",
    "housing": "Moradia",
    "home": "Moradia",
    "health": "Saúde",
    "pharmacy": "Farmácia",
    "entertainment": "Lazer",
    "education": "Educação",
    "shopping": "Vestuário",
    "salary": "Salário",
    "income": "Salário",
    "investment": "Investimentos",
    "investments": "Investimentos",
}


def _to_cents(value: Any) -> int:
    try:
        return int((abs(Decimal(str(value))) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _to_signed_cents(value: Any) -> int:
    try:
        return int((Decimal(str(value)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    except (InvalidOperation, TypeError, ValueError):
        return 0


def _iso_date(value: Any) -> str:
    if isinstance(value, str) and value:
        return value
    return datetime.now(timezone.utc).isoformat()


def _day_from_iso(value: Any, default: int = 1) -> int:
    if not isinstance(value, str) or not value:
        return default
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).day
    except ValueError:
        return default


def _category_name(transaction: dict[str, Any]) -> str:
    category = transaction.get("category")
    if isinstance(category, dict):
        raw = category.get("description") or category.get("name") or ""
    else:
        raw = category or ""
    key = str(raw).strip().lower()
    direct = CATEGORY_MAP.get(key)
    if direct:
        return direct
    for fragment, local_name in CATEGORY_MAP.items():
        if fragment in key:
            return local_name
    return "Outros"


def _find_category_id(user_id: str, transaction_type: int, preferred_name: str) -> str:
    preferred = (
        supabase_client.table("categories")
        .select("id")
        .eq("user_id", user_id)
        .eq("type", transaction_type)
        .eq("name", preferred_name)
        .limit(1)
        .execute()
    )
    if preferred.data:
        return preferred.data[0]["id"]

    fallback = (
        supabase_client.table("categories")
        .select("id")
        .eq("user_id", user_id)
        .eq("type", transaction_type)
        .order("name")
        .limit(1)
        .execute()
    )
    if not fallback.data:
        raise PluggyError("Cadastre ao menos uma categoria de receita e de despesa antes de sincronizar.", 422)
    return fallback.data[0]["id"]


def _connector_info(item: dict[str, Any]) -> tuple[str, Optional[str]]:
    connector = item.get("connector") or {}
    return connector.get("name") or "Instituição financeira", connector.get("imageUrl")


def upsert_connection(user_id: str, item: dict[str, Any]) -> dict[str, Any]:
    connector_name, connector_image = _connector_info(item)
    payload = {
        "user_id": user_id,
        "item_id": item["id"],
        "connector_name": connector_name,
        "connector_image_url": connector_image,
        "status": item.get("status") or "UNKNOWN",
        "execution_status": item.get("executionStatus"),
        "last_successful_update_at": item.get("lastUpdatedAt") or item.get("updatedAt"),
        "next_auto_sync_at": item.get("nextAutoSyncAt"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    result = supabase_client.table("pluggy_connections").upsert(payload, on_conflict="item_id").execute()
    return result.data[0] if result.data else payload


def sync_accounts(client: PluggyClient, user_id: str, item_id: str) -> tuple[int, dict[str, str]]:
    item = client.get_item(item_id)
    upsert_connection(user_id, item)
    connector_name, connector_image = _connector_info(item)
    accounts = client.get_accounts(item_id)
    account_types: dict[str, str] = {}

    for account in accounts:
        account_id = account["id"]
        account_type = account.get("type") or "BANK"
        subtype = account.get("subtype") or "UNKNOWN"
        account_types[account_id] = account_type
        common = {
            "user_id": user_id,
            "item_id": item_id,
            "pluggy_account_id": account_id,
            "name": account.get("name") or connector_name,
            "type": account_type,
            "subtype": subtype,
            "balance": _to_signed_cents(account.get("balance", 0)),
            "currency_code": account.get("currencyCode") or "BRL",
            "number": account.get("number"),
            "institution_name": connector_name,
            "institution_image_url": connector_image,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        supabase_client.table("financial_accounts").upsert(common, on_conflict="pluggy_account_id").execute()

        if account_type == "CREDIT" or subtype == "CREDIT_CARD":
            credit = account.get("creditData") or {}
            limit_cents = max(1, _to_cents(credit.get("creditLimit", 0)))
            card_payload = {
                "user_id": user_id,
                "name": account.get("name") or connector_name,
                "limit": limit_cents,
                "closing_day": _day_from_iso(credit.get("balanceCloseDate")),
                "due_day": _day_from_iso(credit.get("balanceDueDate")),
                "source": "pluggy",
                "pluggy_item_id": item_id,
                "pluggy_account_id": account_id,
                "brand": credit.get("brand"),
                "available_limit": _to_cents(credit.get("availableCreditLimit", 0)),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
            supabase_client.table("credit_cards").upsert(card_payload, on_conflict="pluggy_account_id").execute()

    return len(accounts), account_types


def _existing_imports(user_id: str, transaction_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not transaction_ids:
        return {}
    response = (
        supabase_client.table("transactions")
        .select("pluggy_transaction_id, category_id, description, user_edited")
        .eq("user_id", user_id)
        .in_("pluggy_transaction_id", transaction_ids)
        .execute()
    )
    return {row["pluggy_transaction_id"]: row for row in response.data}


def sync_transactions(
    user_id: str,
    item_id: str,
    account_id: str,
    transactions: list[dict[str, Any]],
) -> int:
    account = (
        supabase_client.table("financial_accounts")
        .select("type")
        .eq("user_id", user_id)
        .eq("pluggy_account_id", account_id)
        .limit(1)
        .execute()
    )
    account_type = account.data[0]["type"] if account.data else "BANK"
    card_id: Optional[str] = None
    closing_day = 1
    if account_type == "CREDIT":
        card = (
            supabase_client.table("credit_cards")
            .select("id, closing_day")
            .eq("user_id", user_id)
            .eq("pluggy_account_id", account_id)
            .limit(1)
            .execute()
        )
        if card.data:
            card_id = card.data[0]["id"]
            closing_day = card.data[0]["closing_day"]

    ids = [transaction["id"] for transaction in transactions if transaction.get("id")]
    existing = _existing_imports(user_id, ids)
    payloads: list[dict[str, Any]] = []

    for transaction in transactions:
        transaction_card_id = card_id
        transaction_closing_day = closing_day
        transaction_id = transaction.get("id")
        amount_value = transaction.get("amount", 0)
        amount_cents = _to_cents(amount_value)
        if not transaction_id or amount_cents == 0:
            continue
        upstream_type = str(transaction.get("type") or "").upper()
        if upstream_type in ("DEBIT", "CREDIT"):
            transaction_type = 1 if upstream_type == "CREDIT" else 0
        else:
            try:
                transaction_type = 1 if Decimal(str(amount_value)) > 0 else 0
            except InvalidOperation:
                continue
        preferred_category = _category_name(transaction)
        raw_category = transaction.get("category")
        normalized_category = str(raw_category or "").strip().lower()

        # A card statement payment also appears on its bank account. Keep the
        # bank-side cash movement and omit the duplicate entry from the card.
        is_card_payment = "credit card payment" in normalized_category
        if account_type == "CREDIT" and is_card_payment:
            continue

        category_id = _find_category_id(user_id, transaction_type, preferred_category)
        merchant_value = transaction.get("merchant")
        merchant = merchant_value if isinstance(merchant_value, dict) else {}
        description = merchant.get("name") or transaction.get("description") or "Transação importada"
        transaction_date = _iso_date(transaction.get("date"))
        imported = existing.get(transaction_id)
        if imported and imported.get("user_edited"):
            category_id = imported["category_id"]
            description = imported.get("description") or description

        invoice_period = None
        if account_type != "CREDIT" and is_card_payment:
            linked_card = (
                supabase_client.table("credit_cards")
                .select("id, closing_day")
                .eq("user_id", user_id)
                .eq("pluggy_item_id", item_id)
                .limit(1)
                .execute()
            )
            if linked_card.data:
                transaction_card_id = linked_card.data[0]["id"]
                transaction_closing_day = linked_card.data[0]["closing_day"]
        if transaction_card_id:
            credit_metadata = transaction.get("creditCardMetadata") or {}
            if isinstance(credit_metadata, dict):
                invoice_period = credit_metadata.get("billForecastDate")
            try:
                date_value = datetime.fromisoformat(transaction_date.replace("Z", "+00:00"))
                year, month = date_value.year, date_value.month
                if date_value.day > transaction_closing_day:
                    month += 1
                    if month == 13:
                        year += 1
                        month = 1
                if not invoice_period:
                    invoice_period = f"{year:04d}-{month:02d}"
            except ValueError:
                invoice_period = None

        payloads.append(
            {
                "user_id": user_id,
                "amount": amount_cents,
                "type": transaction_type,
                "category_id": category_id,
                "description": str(description)[:255],
                "date": transaction_date,
                "is_provision": False,
                "payment_method": "cartao" if account_type == "CREDIT" else "dinheiro",
                "card_id": transaction_card_id,
                "invoice_period": invoice_period,
                "source": "pluggy",
                "pluggy_item_id": item_id,
                "pluggy_account_id": account_id,
                "pluggy_transaction_id": transaction_id,
                "pluggy_status": transaction.get("status"),
                "raw_category": preferred_category,
                "merchant_name": merchant.get("name"),
                "user_edited": bool(imported and imported.get("user_edited")),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        )

    if payloads:
        supabase_client.table("transactions").upsert(payloads, on_conflict="pluggy_transaction_id").execute()
    return len(payloads)


def reconcile_item(client: PluggyClient, user_id: str, item_id: str) -> dict[str, int]:
    account_count, account_types = sync_accounts(client, user_id, item_id)
    transaction_count = 0
    for account_id in account_types:
        transaction_count += sync_transactions(
            user_id,
            item_id,
            account_id,
            client.get_all_transactions(account_id),
        )

    supabase_client.table("pluggy_connections").update(
        {"last_sync_at": datetime.now(timezone.utc).isoformat(), "last_error": None}
    ).eq("item_id", item_id).eq("user_id", user_id).execute()
    return {"accounts": account_count, "transactions": transaction_count}


def delete_imported_item_data(user_id: str, item_id: str) -> None:
    supabase_client.table("transactions").delete().eq("user_id", user_id).eq("pluggy_item_id", item_id).execute()
    supabase_client.table("credit_cards").delete().eq("user_id", user_id).eq("pluggy_item_id", item_id).execute()
    supabase_client.table("financial_accounts").delete().eq("user_id", user_id).eq("item_id", item_id).execute()
    supabase_client.table("pluggy_connections").delete().eq("user_id", user_id).eq("item_id", item_id).execute()
