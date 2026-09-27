"""Import the latest Pluggy data into the current Supabase schema.

Keeps user edits and existing classifications when refreshing transactions.
"""

from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from collections import Counter, defaultdict

from app.database import supabase_client
from app.pluggy import PluggyClient


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _cents(value) -> int:
    return int((Decimal(str(value or 0)) * 100).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _day(value):
    try:
        return int(str(value)[:10].split("-")[2])
    except (ValueError, IndexError, TypeError):
        return None


def _existing(table: str, user_id: str, item_id: str, key: str) -> dict:
    rows = []
    offset = 0
    while True:
        page = (supabase_client.table(table).select("*").eq("user_id", user_id)
                .eq("pluggy_item_id" if table != "investments" else "pluggy_connection_id", item_id)
                .range(offset, offset + 999).execute().data or [])
        rows.extend(page)
        if len(page) < 1000:
            return {str(row[key]): row for row in rows if row.get(key)}
        offset += 1000


def _save_rows(table: str, payloads: list[dict]) -> None:
    # The current database does not define unique constraints on every Pluggy ID.
    # Existing records are updated by primary key; only unseen IDs are inserted.
    existing = [payload for payload in payloads if payload.get("id")]
    fresh = [payload for payload in payloads if not payload.get("id")]
    for payload in existing:
        row_id = payload["id"]
        supabase_client.table(table).update({k: v for k, v in payload.items() if k != "id"}) \
            .eq("id", row_id).eq("user_id", payload["user_id"]).execute()
    for offset in range(0, len(fresh), 100):
        supabase_client.table(table).insert(fresh[offset:offset + 100]).execute()


def _classification(transaction: dict, account_type: str) -> tuple[str, str, bool]:
    upstream = str(transaction.get("type") or "").upper()
    raw_amount = _cents(transaction.get("amount"))
    kind = "income" if upstream == "CREDIT" or (upstream not in ("DEBIT", "CREDIT") and raw_amount > 0) else "expense"
    description = str(transaction.get("description") or "").lower()
    category = str(transaction.get("category") or "").lower()
    payment = any(word in description or word in category for word in
                  ("pagamento fatura", "pagto fatura", "pagto cartão", "pagto cartao", "credit card payment"))
    operation = str(transaction.get("operationType") or "").upper()
    if category == "same person transfer":
        nature = "internal_transfer"
    elif payment:
        nature = "card_bill_payment"
    elif account_type == "CREDIT":
        nature = "credit_card_refund" if kind == "income" else "credit_card_purchase"
    elif account_type == "INVESTMENT" or operation == "RESGATE_APLIC_FINANCEIRA" or category == "proceeds interests and dividends":
        nature = "investment_transfer"
    elif operation in ("PIX", "TED"):
        nature = "transfer"
    else:
        nature = kind
    return kind, nature, nature in ("internal_transfer", "card_bill_payment", "investment_transfer")


def _import_item(client: PluggyClient, user_id: str, connection: dict,
                 category_map: dict[tuple[str, str], str]) -> dict:
    item_id = connection["item_id"]
    item = client.get_item(item_id)
    if item.get("clientUserId") and item["clientUserId"] != user_id:
        raise ValueError("A instituição não pertence ao usuário atual.")
    institution = connection.get("institution_name") or (item.get("connector") or {}).get("name") or "Instituição"
    account_response = client._request("GET", "/accounts", params={"itemId": item_id})
    accounts = account_response if isinstance(account_response, list) else account_response.get("results", [])
    existing_accounts = _existing("financial_accounts", user_id, item_id, "pluggy_account_id")
    existing_cards = {str(card["pluggy_account_id"]): card for card in
                      (supabase_client.table("credit_cards").select("*").eq("user_id", user_id)
                       .eq("pluggy_connection_id", connection["id"]).execute().data or [])
                      if card.get("pluggy_account_id")}
    account_payloads = []
    card_payloads = []
    timestamp = _now()
    for account in accounts:
        account_id = str(account["id"])
        existing = existing_accounts.get(account_id, {})
        number = str(account.get("number") or "")
        account_payloads.append({
            **({"id": existing["id"]} if existing else {}),
            "user_id": user_id, "pluggy_connection_id": connection["id"],
            "pluggy_item_id": item_id, "pluggy_account_id": account_id,
            "institution_name": institution, "name": account.get("name") or institution,
            "type": account.get("type") or "BANK", "subtype": account.get("subtype"),
            "currency": account.get("currencyCode") or "BRL",
            "balance_cents": _cents(account.get("balance")),
            "number_masked": ("•••• " + number[-4:]) if number else None,
            "raw_payload": account, "last_synced_at": timestamp, "updated_at": timestamp,
        })
    _save_rows("financial_accounts", account_payloads)
    current_accounts = _existing("financial_accounts", user_id, item_id, "pluggy_account_id")
    for account in accounts:
        if account.get("type") != "CREDIT" and account.get("subtype") != "CREDIT_CARD":
            continue
        account_id = str(account["id"])
        existing = existing_cards.get(account_id, {})
        credit = account.get("creditData") or {}
        card_payloads.append({
            **({"id": existing["id"]} if existing else {}),
            "user_id": user_id, "name": account.get("name") or institution,
            "institution_name": institution, "origin": "PLUGGY",
            "financial_account_id": current_accounts[account_id]["id"],
            "pluggy_account_id": account_id, "pluggy_connection_id": connection["id"],
            "limit_total_cents": _cents(credit.get("creditLimit")),
            "limit_available_cents": _cents(credit["availableCreditLimit"]) if credit.get("availableCreditLimit") is not None else None,
            "current_bill_cents": abs(_cents(account.get("balance"))),
            "closing_day": existing.get("closing_day") or _day(credit.get("balanceCloseDate")),
            "due_day": existing.get("due_day") or _day(credit.get("balanceDueDate")),
            "raw_payload": account, "updated_at": timestamp,
        })
    _save_rows("credit_cards", card_payloads)
    cards = {str(card["pluggy_account_id"]): card for card in
             (supabase_client.table("credit_cards").select("id,pluggy_account_id")
              .eq("user_id", user_id).eq("pluggy_connection_id", connection["id"]).execute().data or [])}

    old_transactions = _existing("transactions", user_id, item_id, "pluggy_transaction_id")
    changes = []
    unchanged = 0
    inserted = 0
    updated = 0
    category_assignments = defaultdict(list)
    for account in accounts:
        account_id = str(account["id"])
        for transaction in client.get_all_transactions(account_id):
            transaction_id = transaction.get("id")
            if not transaction_id:
                continue
            transaction_id = str(transaction_id)
            previous = old_transactions.get(transaction_id)
            kind, nature, excluded = _classification(transaction, str(account.get("type") or "BANK"))
            raw_category = str(transaction.get("category") or "").strip().lower()
            suggested_category = category_map.get((raw_category, kind))
            if previous and previous.get("raw_payload") == transaction:
                if excluded and not previous.get("excluded_from_income_expense"):
                    (supabase_client.table("transactions").update({
                        "transaction_nature": nature,
                        "is_internal_transfer": nature == "internal_transfer",
                        "is_card_bill_payment": nature == "card_bill_payment",
                        "excluded_from_income_expense": True,
                        "excluded_from_category_analytics": True,
                    }).eq("id", previous["id"]).eq("user_id", user_id).execute())
                    updated += 1
                    continue
                if not previous.get("category_id") and suggested_category and not previous.get("user_edited_category"):
                    category_assignments[suggested_category].append(previous["id"])
                    updated += 1
                else:
                    unchanged += 1
                continue
            raw_amount = _cents(transaction.get("amount"))
            if raw_amount == 0:
                continue
            merchant = transaction.get("merchant") or {}
            if not isinstance(merchant, dict):
                merchant = {}
            description = str(merchant.get("name") or transaction.get("description") or "Lançamento")[:255]
            payload = {
                **({"id": previous["id"]} if previous else {}),
                "user_id": user_id, "source": "pluggy",
                "pluggy_connection_id": connection["id"], "pluggy_item_id": item_id,
                "pluggy_account_id": account_id, "pluggy_transaction_id": transaction_id,
                "financial_account_id": current_accounts[account_id]["id"],
                "credit_card_id": cards.get(account_id, {}).get("id"),
                "description": previous["description"] if previous and previous.get("user_edited_description") else description,
                "original_description": description,
                "category_id": (previous.get("category_id") or suggested_category) if previous else suggested_category,
                "amount_cents": abs(raw_amount), "raw_amount_cents": raw_amount,
                "transaction_type": previous.get("transaction_type", kind) if previous else kind,
                "transaction_nature": previous.get("transaction_nature", nature) if previous else nature,
                "transaction_date": str(transaction.get("date") or "")[:10],
                "payment_method": "card" if account.get("type") == "CREDIT" else "transfer",
                "is_provision": previous.get("is_provision", False) if previous else False,
                "is_internal_transfer": previous.get("is_internal_transfer", False) if previous else False,
                "is_card_bill_payment": previous.get("is_card_bill_payment", nature == "card_bill_payment") if previous else nature == "card_bill_payment",
                "excluded_from_income_expense": previous.get("excluded_from_income_expense", excluded) if previous else excluded,
                "excluded_from_category_analytics": previous.get("excluded_from_category_analytics", excluded) if previous else excluded,
                "user_edited_description": previous.get("user_edited_description", False) if previous else False,
                "user_edited_category": previous.get("user_edited_category", False) if previous else False,
                "raw_payload": transaction,
                "pluggy_created_at": transaction.get("createdAt"),
                "pluggy_updated_at": transaction.get("updatedAt"),
                "updated_at": timestamp,
            }
            if previous:
                updated += 1
            else:
                inserted += 1
            changes.append(payload)
    _save_rows("transactions", changes)
    for category_id, row_ids in category_assignments.items():
        for offset in range(0, len(row_ids), 100):
            (supabase_client.table("transactions").update({"category_id": category_id})
             .eq("user_id", user_id).in_("id", row_ids[offset:offset + 100]).execute())

    existing_investments = _existing("investments", user_id, connection["id"], "pluggy_investment_id")
    investment_payloads = []
    page_number = 1
    while True:
        page = client._request("GET", "/investments", params={
            "itemId": item_id, "page": page_number, "pageSize": 500,
        })
        for investment in page.get("results", []):
            investment_id = str(investment["id"])
            previous = existing_investments.get(investment_id, {})
            if previous.get("raw_payload") == investment:
                continue
            investment_payloads.append({
                **({"id": previous["id"]} if previous else {}),
                "user_id": user_id, "pluggy_connection_id": connection["id"],
                "pluggy_investment_id": investment_id,
                "name": investment.get("name") or "Investimento",
                "institution_name": institution, "type": investment.get("type"),
                "status": investment.get("status"),
                "currency": investment.get("currencyCode") or "BRL",
                "balance_cents": _cents(investment.get("balance")),
                "raw_payload": investment, "last_synced_at": timestamp,
                "updated_at": timestamp,
            })
        if page_number >= page.get("totalPages", 1):
            break
        page_number += 1
    _save_rows("investments", investment_payloads)

    supabase_client.table("pluggy_connections").update({
        "status": item.get("status") or connection.get("status") or "UNKNOWN",
        "sync_status": "success", "last_sync_error": None,
        "last_sync_at": _now(), "last_successful_sync_at": _now(),
        "accounts_count": len(account_payloads), "cards_count": len(card_payloads),
        "investments_count": len(existing_investments) + sum(1 for item in investment_payloads if "id" not in item),
        "raw_payload": item, "updated_at": _now(),
    }).eq("id", connection["id"]).eq("user_id", user_id).execute()
    return {"accounts_updated": len(account_payloads), "cards_updated": len(card_payloads),
            "investments_updated": len(investment_payloads),
            "transactions_inserted": inserted, "transactions_updated": updated,
            "transactions_unchanged": unchanged}


def sync_all(user_id: str, client: PluggyClient) -> dict:
    connections = (supabase_client.table("pluggy_connections").select("*")
                   .eq("user_id", user_id).execute().data or [])
    votes = defaultdict(Counter)
    offset = 0
    while True:
        page = (supabase_client.table("transactions")
                .select("transaction_type,category_id,raw_payload,user_edited_category")
                .eq("user_id", user_id).range(offset, offset + 999).execute().data or [])
        for row in page:
            if row.get("category_id") and not row.get("user_edited_category"):
                raw_category = str((row.get("raw_payload") or {}).get("category") or "").strip().lower()
                if raw_category:
                    votes[(raw_category, row["transaction_type"])][row["category_id"]] += 1
        if len(page) < 1000:
            break
        offset += 1000
    category_map = {key: counts.most_common(1)[0][0] for key, counts in votes.items()}
    run = supabase_client.table("pluggy_sync_runs").insert({
        "user_id": user_id, "status": "running", "institutions_total": len(connections),
    }).execute().data[0]
    totals = {key: 0 for key in ("accounts_updated", "cards_updated", "investments_updated", "transactions_inserted",
                                 "transactions_updated", "transactions_unchanged")}
    errors = []
    succeeded = 0
    for connection in connections:
        try:
            result = _import_item(client, user_id, connection, category_map)
            succeeded += 1
            for key in totals:
                totals[key] += result[key]
        except Exception as error:
            message = f"{connection.get('institution_name') or 'Instituição'}: {error}"
            errors.append(message)
            supabase_client.table("pluggy_connections").update({
                "sync_status": "error", "last_sync_error": str(error), "last_sync_at": _now(),
            }).eq("id", connection["id"]).eq("user_id", user_id).execute()
    result = {**totals, "institutions_succeeded": succeeded,
              "institutions_failed": len(errors), "errors": errors,
              "status": "success" if not errors else "error", "finished_at": _now()}
    supabase_client.table("pluggy_sync_runs").update(result).eq("id", run["id"]).eq("user_id", user_id).execute()
    return {"sync_id": run["id"], "connections_queued": len(connections), "message": "Importação concluída.", **result}
