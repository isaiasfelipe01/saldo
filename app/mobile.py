"""Read model for the native app, accepting both legacy and current finance rows."""

from datetime import date
from uuid import UUID
import secrets

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from app.database import supabase_client
from app.config import settings

router = APIRouter(prefix="/mobile", tags=["mobile"])


def _user_id(x_user_id: str = Header(...), x_app_token: str = Header(...)) -> str:
    if not settings.app_api_token or not secrets.compare_digest(x_app_token, settings.app_api_token):
        raise HTTPException(status_code=403, detail="Acesso ao aplicativo não autorizado.")
    try:
        user_id = str(UUID(x_user_id))
    except ValueError as error:
        raise HTTPException(status_code=400, detail="X-User-ID inválido.") from error
    allowed = settings.app_user_id or "00000000-0000-0000-0000-000000000001"
    if user_id != allowed:
        raise HTTPException(status_code=403, detail="Usuário não autorizado.")
    return user_id


def _rows(table: str, user_id: str) -> list[dict]:
    """PostgREST caps responses; page so accounts and long histories are complete."""
    rows = []
    offset = 0
    while True:
        page = (
            supabase_client.table(table).select("*").eq("user_id", user_id)
            .range(offset, offset + 999).execute().data or []
        )
        rows.extend(page)
        if len(page) < 1000:
            return rows
        offset += 1000


def _month_transactions(user_id: str, month: str) -> list[dict]:
    year, number = map(int, month.split("-"))
    start = date(year, number, 1).isoformat()
    end = date(year + (number == 12), number % 12 + 1, 1).isoformat()
    for column in ("transaction_date", "date"):
        try:
            rows = []
            offset = 0
            while True:
                page = (supabase_client.table("transactions").select("*").eq("user_id", user_id)
                        .gte(column, start).lt(column, end).range(offset, offset + 999)
                        .execute().data or [])
                rows.extend(page)
                if len(page) < 1000:
                    return rows
                offset += 1000
        except Exception as error:
            if column == "date" or "42703" not in str(error):
                raise
    return []


def _category_type(value) -> str:
    return "income" if value in (1, "1", "income", "receita") else "expense"


def _transaction_type(row: dict) -> str:
    value = row.get("transaction_type", row.get("type"))
    return _category_type(value)


def _normalise_transaction(row: dict, cards_by_account: dict) -> dict:
    transaction_type = _transaction_type(row)
    card_id = row.get("credit_card_id") or row.get("card_id")
    account_id = row.get("financial_account_id") or row.get("pluggy_account_id")
    if not card_id and account_id:
        card_id = cards_by_account.get(account_id)
    nature = row.get("transaction_nature")
    if not nature:
        if card_id and row.get("payment_method") == "cartao":
            nature = "credit_card_purchase" if transaction_type == "expense" else "credit_card_refund"
        elif card_id and row.get("payment_method") == "dinheiro":
            nature = "card_bill_payment"
        else:
            nature = transaction_type
    excluded = row.get("excluded_from_income_expense")
    if excluded is None:
        excluded = nature in ("internal_transfer", "investment_transfer", "card_bill_payment")
    excluded_category = row.get("excluded_from_category_analytics")
    if excluded_category is None:
        excluded_category = nature in ("internal_transfer", "investment_transfer", "card_bill_payment")
    return {
        "id": str(row["id"]),
        "description": row.get("description") or row.get("merchant_name") or "Lançamento",
        "amount_cents": abs(int(row.get("amount_cents", row.get("amount", 0)) or 0)),
        "transaction_date": str(row.get("transaction_date") or row.get("date") or "")[:10],
        "transaction_type": transaction_type,
        "transaction_nature": nature,
        "category_id": row.get("category_id"),
        "financial_account_id": account_id,
        "credit_card_id": card_id,
        "pluggy_transaction_id": row.get("pluggy_transaction_id"),
        "is_provision": bool(row.get("is_provision", False)),
        "excluded_from_income_expense": bool(excluded),
        "excluded_from_category_analytics": bool(excluded_category),
    }


def _summary_totals(transactions: list[dict]) -> tuple[int, int, int]:
    """Separate account cash flow from card purchases and refunds."""
    income = account_expense = card_expense = 0
    for row in transactions:
        if (row["is_provision"] or row["excluded_from_income_expense"] or
                row["transaction_nature"] in ("card_bill_payment", "internal_transfer", "investment_transfer")):
            continue
        nature = row["transaction_nature"]
        amount = row["amount_cents"]
        if nature in ("credit_card_purchase", "credit_card_refund"):
            card_expense += -amount if nature == "credit_card_refund" else amount
        elif row["transaction_type"] == "income":
            income += amount
        elif row["transaction_type"] == "expense":
            account_expense += amount
    return income, account_expense, card_expense


@router.get("/overview")
def overview(
    month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
    user_id: str = Depends(_user_id),
):
    try:
        categories = _rows("categories", user_id)
        accounts = _rows("financial_accounts", user_id)
        cards = _rows("credit_cards", user_id)
        investments = _rows("investments", user_id)
        connections = _rows("pluggy_connections", user_id)
        raw_transactions = _month_transactions(user_id, month)
    except Exception as error:
        raise HTTPException(status_code=503, detail="Não foi possível consultar o banco de dados.") from error

    cards_by_account = {
        str(card["pluggy_account_id"]): str(card["id"])
        for card in cards if card.get("pluggy_account_id")
    }
    transactions = [
        _normalise_transaction(row, cards_by_account)
        for row in raw_transactions
        if str(row.get("transaction_date") or row.get("date") or "")[:7] == month
    ]
    transactions.sort(key=lambda row: (row["transaction_date"], row["id"]), reverse=True)

    income, expense, card_expense = _summary_totals(transactions)
    bank_balance = sum(int(account.get("balance_cents", account.get("balance", 0)) or 0)
                       for account in accounts if str(account.get("type", "")).upper() == "BANK")

    return {
        "month": month,
        "summary": {"income_cents": income, "expense_cents": expense,
                    "card_expense_cents": card_expense,
                    "bank_balance_cents": bank_balance},
        "transactions": transactions,
        "accounts": [{
            "id": str(account["id"]),
            "name": account.get("name") or "Conta",
            "institution_name": account.get("institution_name") or "Instituição",
            "type": account.get("type") or "BANK",
            "subtype": account.get("subtype") or "",
            "number_masked": account.get("number_masked") or account.get("number"),
            "balance_cents": int(account.get("balance_cents", account.get("balance", 0)) or 0),
        } for account in accounts],
        "cards": [{
            "id": str(card["id"]), "name": card.get("name") or "Cartão",
            "institution_name": card.get("institution_name") or card.get("name") or "Instituição",
            "limit_total_cents": int(card.get("limit_total_cents", card.get("limit", 0)) or 0),
            "limit_available_cents": card.get("limit_available_cents", card.get("available_limit")),
            "current_bill_cents": int(card.get("current_bill_cents", 0) or 0),
            "due_day": card.get("due_day"),
        } for card in cards],
        "investments": [{
            "id": str(investment["id"]),
            "name": investment.get("name") or "Investimento",
            "institution_name": investment.get("institution_name") or "Instituição",
            "type": investment.get("type") or "INVESTMENT",
            "status": investment.get("status"),
            "balance_cents": int(investment.get("balance_cents", 0) or 0),
        } for investment in investments],
        "categories": [{"id": str(cat["id"]), "name": cat.get("name") or "Sem categoria",
                        "type": _category_type(cat.get("type"))} for cat in categories],
        "connections": [{
            "item_id": str(connection["item_id"]),
            "institution_name": connection.get("institution_name") or connection.get("connector_name") or "Instituição",
            "status": connection.get("sync_status") or connection.get("status") or "UNKNOWN",
            "last_sync_at": connection.get("last_sync_at"),
            "last_error": connection.get("last_sync_error") or connection.get("last_error"),
        } for connection in connections],
    }
