"""The monthly result is account cash flow; card activity has its own total."""

from app.mobile import _normalise_transaction, _summary_totals


def test_card_purchases_refunds_and_bill_payment_are_separate():
    rows = [
        {"id": "income", "transaction_type": "income", "transaction_nature": "income", "amount_cents": 200_000},
        {"id": "pix", "transaction_type": "expense", "transaction_nature": "expense", "amount_cents": 40_000},
        {"id": "purchase", "transaction_type": "expense", "transaction_nature": "credit_card_purchase", "amount_cents": 90_000},
        {"id": "refund", "transaction_type": "income", "transaction_nature": "credit_card_refund", "amount_cents": 10_000},
        {"id": "bill", "transaction_type": "expense", "transaction_nature": "card_bill_payment", "amount_cents": 80_000,
         "excluded_from_income_expense": False},
        {"id": "transfer", "transaction_type": "expense", "transaction_nature": "internal_transfer", "amount_cents": 30_000,
         "excluded_from_income_expense": True},
        {"id": "provision", "transaction_type": "expense", "transaction_nature": "expense", "amount_cents": 5_000,
         "is_provision": True},
    ]
    normalised = [_normalise_transaction(row, {}) for row in rows]

    assert _summary_totals(normalised) == (200_000, 40_000, 80_000)


def test_legacy_card_account_is_recognised():
    purchase = _normalise_transaction({
        "id": "purchase", "type": 0, "amount": 12_000,
        "payment_method": "cartao", "financial_account_id": "credit-account",
    }, {"credit-account": "card-id"})
    bill = _normalise_transaction({
        "id": "bill", "type": 0, "amount": 12_000,
        "payment_method": "dinheiro", "card_id": "card-id",
    }, {})

    assert purchase["transaction_nature"] == "credit_card_purchase"
    assert bill["transaction_nature"] == "card_bill_payment"
    assert _summary_totals([purchase, bill]) == (0, 0, 12_000)
