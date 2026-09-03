import calendar
import secrets
import uuid
from uuid import UUID
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from fastapi import BackgroundTasks, Body, Depends, FastAPI, Header, HTTPException, Query, status
from fastapi.middleware.cors import CORSMiddleware

from app.database import supabase_client
from app.schemas import (
    CategoryCreate,
    CategoryResponse,
    CategoryUpdate,
    CategoryBreakdownItem,
    SummaryResponse,
    TransactionCreate,
    TransactionResponse,
    TransactionUpdate,
    CreditCardCreate,
    CreditCardResponse,
    CreditCardUpdate,
    CreditCardSummaryResponse,
    BudgetCreate,
    BudgetResponse,
    MonthlyTrendResponse,
    PluggyConnectionResponse,
    PluggyConnectTokenRequest,
    PluggyConnectTokenResponse,
    PluggyItemRegisterRequest,
    PluggySyncResponse,
)
from app.config import settings
from app.pluggy import (
    PluggyClient,
    PluggyError,
    delete_imported_item_data,
    reconcile_item,
    sync_accounts,
    sync_transactions,
    upsert_connection,
)

app = FastAPI(
    title="MeuFinanças API",
    description="Backend API for personal finance tracking app using Supabase",
    version="1.0.0"
)

# Enable CORS for cross-origin client integration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_pluggy_client: Optional[PluggyClient] = None


def get_pluggy_client() -> PluggyClient:
    global _pluggy_client
    if not settings.pluggy_client_id or not settings.pluggy_client_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="A integração Pluggy ainda não foi configurada no servidor.",
        )
    if _pluggy_client is None:
        _pluggy_client = PluggyClient(settings.pluggy_client_id, settings.pluggy_client_secret)
    return _pluggy_client


def pluggy_webhook_url() -> str:
    if not settings.pluggy_webhook_url or not settings.pluggy_webhook_secret:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Configure PLUGGY_WEBHOOK_URL e PLUGGY_WEBHOOK_SECRET no servidor.",
        )
    parts = urlsplit(settings.pluggy_webhook_url)
    if parts.scheme != "https" or not parts.netloc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="PLUGGY_WEBHOOK_URL precisa ser uma URL HTTPS pública.",
        )
    query = dict(parse_qsl(parts.query, keep_blank_values=True))
    query["token"] = settings.pluggy_webhook_secret
    return urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), parts.fragment))


def raise_pluggy_http_error(error: PluggyError) -> None:
    if error.status_code in (400, 404, 409, 422):
        http_status = error.status_code
    else:
        http_status = status.HTTP_502_BAD_GATEWAY
    raise HTTPException(status_code=http_status, detail=str(error))


# Helper: Validate and return User ID header, and ensure the user and default categories exist
def get_user_id(x_user_id: str = Header(..., description="UUID of the authenticated user")) -> str:
    try:
        user_uuid = uuid.UUID(x_user_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Header 'X-User-ID' is not a valid UUID format."
        )
    
    user_id_str = str(user_uuid)
    
    # Auto-registration / check user existence in Supabase
    try:
        user_check = supabase_client.table("users").select("id").eq("id", user_id_str).execute()
        if not user_check.data:
            # User doesn't exist, create them
            email = f"user_{user_id_str[:8]}@meufinancas.com"
            supabase_client.table("users").insert({"id": user_id_str, "email": email}).execute()
            
            # Seed default categories for this user
            default_categories = [
                # Expenses (type = 0)
                {"name": "Alimentação", "icon": "🍔", "type": 0, "is_default": True, "user_id": user_id_str},
                {"name": "Transporte", "icon": "🚌", "type": 0, "is_default": True, "user_id": user_id_str},
                {"name": "Moradia", "icon": "🏠", "type": 0, "is_default": True, "user_id": user_id_str},
                {"name": "Saúde", "icon": "🏥", "type": 0, "is_default": True, "user_id": user_id_str},
                {"name": "Lazer", "icon": "🎮", "type": 0, "is_default": True, "user_id": user_id_str},
                {"name": "Educação", "icon": "📚", "type": 0, "is_default": True, "user_id": user_id_str},
                {"name": "Vestuário", "icon": "👕", "type": 0, "is_default": True, "user_id": user_id_str},
                {"name": "Supermercado", "icon": "🛒", "type": 0, "is_default": True, "user_id": user_id_str},
                {"name": "Farmácia", "icon": "💊", "type": 0, "is_default": True, "user_id": user_id_str},
                {"name": "Outros", "icon": "📦", "type": 0, "is_default": True, "user_id": user_id_str},
                # Income (type = 1)
                {"name": "Salário", "icon": "💰", "type": 1, "is_default": True, "user_id": user_id_str},
                {"name": "Freelance", "icon": "💻", "type": 1, "is_default": True, "user_id": user_id_str},
                {"name": "Investimentos", "icon": "📈", "type": 1, "is_default": True, "user_id": user_id_str},
                {"name": "Presentes", "icon": "🎁", "type": 1, "is_default": True, "user_id": user_id_str},
                {"name": "Outros", "icon": "📦", "type": 1, "is_default": True, "user_id": user_id_str},
            ]
            supabase_client.table("categories").insert(default_categories).execute()
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Database initialization failed for user: {str(e)}"
        )
        
    return user_id_str


def calculate_invoice_period(date_val: datetime, closing_day: int) -> str:
    if date_val.tzinfo is None:
        date_val = date_val.replace(tzinfo=timezone.utc)
    
    year = date_val.year
    month = date_val.month
    
    _, last_day = calendar.monthrange(year, month)
    actual_closing_day = min(closing_day, last_day)
    closing_date = datetime(year, month, actual_closing_day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    
    if date_val <= closing_date:
        return f"{year:04d}-{month:02d}"
    else:
        next_month_date = add_months(closing_date, 1)
        return f"{next_month_date.year:04d}-{next_month_date.month:02d}"


def get_active_invoice_cycle(card: dict, current_time: datetime):
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
        
    year = current_time.year
    month = current_time.month
    
    _, last_day = calendar.monthrange(year, month)
    actual_closing_day = min(card["closing_day"], last_day)
    closing_datetime = datetime(year, month, actual_closing_day, 23, 59, 59, 999999, tzinfo=timezone.utc)
    
    if current_time <= closing_datetime:
        invoice_period = f"{year:04d}-{month:02d}"
        cycle_end = closing_datetime
        prev_month_date = add_months(closing_datetime, -1)
        prev_year = prev_month_date.year
        prev_month = prev_month_date.month
        _, prev_last_day = calendar.monthrange(prev_year, prev_month)
        prev_actual_closing_day = min(card["closing_day"], prev_last_day)
        cycle_start = datetime(prev_year, prev_month, prev_actual_closing_day, 0, 0, 0, tzinfo=timezone.utc) + timedelta(days=1)
    else:
        next_month_date = add_months(closing_datetime, 1)
        next_year = next_month_date.year
        next_month = next_month_date.month
        invoice_period = f"{next_year:04d}-{next_month:02d}"
        _, next_last_day = calendar.monthrange(next_year, next_month)
        next_actual_closing_day = min(card["closing_day"], next_last_day)
        cycle_end = datetime(next_year, next_month, next_actual_closing_day, 23, 59, 59, 999999, tzinfo=timezone.utc)
        cycle_start = closing_datetime + timedelta(days=1)
        
    return invoice_period, cycle_start, cycle_end


def check_and_generate_invoice_provisions(user_id: str):
    try:
        cards_response = supabase_client.table("credit_cards").select("*").eq("user_id", user_id).execute()
        cards = cards_response.data
        if not cards:
            return
            
        current_time = datetime.now(timezone.utc)
        
        for card in cards:
            # Pluggy already supplies card activity and billing dates. Creating
            # a synthetic manual invoice would duplicate Open Finance data.
            if card.get("source") == "pluggy":
                continue
            closing_day = card["closing_day"]
            due_day = card["due_day"]
            card_id = card["id"]
            card_name = card["name"]
            
            for months_offset in [-1, 0]:
                check_date = add_months(current_time, months_offset)
                year = check_date.year
                month = check_date.month
                month_str = f"{year:04d}-{month:02d}"
                
                _, last_day = calendar.monthrange(year, month)
                actual_closing_day = min(closing_day, last_day)
                closing_datetime = datetime(year, month, actual_closing_day, 23, 59, 59, 999999, tzinfo=timezone.utc)
                
                if current_time > closing_datetime:
                    tx_check = supabase_client.table("transactions").select("id")\
                        .eq("user_id", user_id)\
                        .eq("card_id", card_id)\
                        .eq("invoice_period", month_str)\
                        .eq("payment_method", "dinheiro")\
                        .eq("is_provision", True)\
                        .execute()
                    
                    if not tx_check.data:
                        prev_date = add_months(closing_datetime, -1)
                        prev_year = prev_date.year
                        prev_month = prev_date.month
                        _, prev_last_day = calendar.monthrange(prev_year, prev_month)
                        prev_actual_closing_day = min(closing_day, prev_last_day)
                        
                        start_cycle = datetime(prev_year, prev_month, prev_actual_closing_day, 0, 0, 0, tzinfo=timezone.utc) + timedelta(days=1)
                        end_cycle = closing_datetime
                        
                        tx_response = supabase_client.table("transactions").select("amount, type")\
                            .eq("user_id", user_id)\
                            .eq("card_id", card_id)\
                            .eq("payment_method", "cartao")\
                            .eq("is_provision", False)\
                            .gte("date", start_cycle.isoformat())\
                            .lte("date", end_cycle.isoformat())\
                            .execute()
                            
                        total_amount = sum_card_activity(tx_response.data)
                        
                        if total_amount > 0:
                            if due_day > closing_day:
                                due_year = year
                                due_month = month
                            else:
                                next_due_date = add_months(closing_datetime, 1)
                                due_year = next_due_date.year
                                due_month = next_due_date.month
                                
                            _, due_last_day = calendar.monthrange(due_year, due_month)
                            actual_due_day = min(due_day, due_last_day)
                            due_datetime = datetime(due_year, due_month, actual_due_day, 12, 0, 0, tzinfo=timezone.utc)
                            
                            cat_response = supabase_client.table("categories").select("id").eq("user_id", user_id).eq("name", "Outros").eq("type", 0).execute()
                            if cat_response.data:
                                category_id = cat_response.data[0]["id"]
                            else:
                                cat_any = supabase_client.table("categories").select("id").eq("user_id", user_id).eq("type", 0).limit(1).execute()
                                category_id = cat_any.data[0]["id"] if cat_any.data else None
                                
                            if category_id:
                                provision_payload = {
                                    "amount": total_amount,
                                    "type": 0,
                                    "category_id": category_id,
                                    "description": f"Fatura {card_name} - {month_str}",
                                    "date": due_datetime.isoformat(),
                                    "user_id": user_id,
                                    "is_provision": True,
                                    "payment_method": "dinheiro",
                                    "card_id": card_id,
                                    "invoice_period": month_str
                                }
                                supabase_client.table("transactions").insert(provision_payload).execute()
    except Exception as e:
        print(f"Error checking invoice provisions: {str(e)}")


# Helper: Parse month string "YYYY-MM" to ISO boundary timestamps in UTC
def get_month_boundaries(month_str: str):
    try:
        year_str, month_part = month_str.split("-")
        year = int(year_str)
        month = int(month_part)
        if not (1 <= month <= 12):
            raise ValueError()
        
        # Start of month in UTC
        start_date = datetime(year, month, 1, 0, 0, 0, tzinfo=timezone.utc)
        # End of month
        _, last_day = calendar.monthrange(year, month)
        end_date = datetime(year, month, last_day, 23, 59, 59, 999999, tzinfo=timezone.utc)
        
        return start_date.isoformat(), end_date.isoformat()
    except Exception:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid month format. Expected 'YYYY-MM'."
        )


# --- CATEGORIES ENDPOINTS ---

@app.get("/categories", response_model=List[CategoryResponse])
def list_categories(user_id: str = Depends(get_user_id)):
    try:
        response = supabase_client.table("categories").select("*").eq("user_id", user_id).execute()
        return response.data
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.post("/categories", response_model=CategoryResponse, status_code=status.HTTP_201_CREATED)
def create_category(category: CategoryCreate, user_id: str = Depends(get_user_id)):
    try:
        # Check unique constraint (user_id + name) to prevent DB exception and return 400
        name_check = supabase_client.table("categories").select("id").eq("user_id", user_id).eq("name", category.name).execute()
        if name_check.data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Category with name '{category.name}' already exists."
            )

        cat_data = {
            "name": category.name,
            "icon": category.icon,
            "type": category.type,
            "is_default": False,
            "user_id": user_id
        }
        response = supabase_client.table("categories").insert(cat_data).execute()
        return response.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.put("/categories/{id}", response_model=CategoryResponse)
def update_category(id: UUID, category_data: CategoryUpdate, user_id: str = Depends(get_user_id)):
    try:
        # Check category existence and user ownership
        cat_check = supabase_client.table("categories").select("*").eq("id", str(id)).eq("user_id", user_id).execute()
        if not cat_check.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found.")
        
        category = cat_check.data[0]
        if category.get("is_default"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Default categories cannot be modified."
            )
            
        # Check uniqueness if name changes
        if category.get("name") != category_data.name:
            name_check = supabase_client.table("categories").select("id").eq("user_id", user_id).eq("name", category_data.name).execute()
            if name_check.data:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail=f"Category with name '{category_data.name}' already exists."
                )

        update_payload = {
            "name": category_data.name,
            "icon": category_data.icon
        }
        response = supabase_client.table("categories").update(update_payload).eq("id", str(id)).execute()
        return response.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.delete("/categories/{id}")
def delete_category(id: UUID, user_id: str = Depends(get_user_id)):
    try:
        # Check category existence and default status
        cat_check = supabase_client.table("categories").select("*").eq("id", str(id)).eq("user_id", user_id).execute()
        if not cat_check.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Category not found.")
        
        category = cat_check.data[0]
        if category.get("is_default"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Default categories cannot be deleted."
            )
            
        cat_type = category.get("type")
        
        # Locate the default fallback category "Outros" for this user and type
        outros_check = supabase_client.table("categories").select("*").eq("user_id", user_id).eq("name", "Outros").eq("type", cat_type).execute()
        
        if not outros_check.data:
            # Recreate fallback "Outros" if missing
            outros_payload = {
                "name": "Outros",
                "icon": "📦",
                "type": cat_type,
                "is_default": True,
                "user_id": user_id
            }
            outros_insert = supabase_client.table("categories").insert(outros_payload).execute()
            outros_id = outros_insert.data[0].get("id")
        else:
            outros_id = outros_check.data[0].get("id")
            
        # Reassign all transactions in this category to the fallback "Outros" category
        supabase_client.table("transactions").update({"category_id": outros_id}).eq("category_id", str(id)).eq("user_id", user_id).execute()
        
        # Safely delete the custom category
        supabase_client.table("categories").delete().eq("id", str(id)).execute()
        
        return {"message": "Category deleted successfully. Transactions reassigned to 'Outros'."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# --- TRANSACTIONS ENDPOINTS ---

@app.get("/transactions", response_model=List[TransactionResponse])
def list_transactions(
    month: str = Query(..., description="Format 'YYYY-MM'"),
    search: Optional[str] = Query(None, description="Search in description"),
    category_id: Optional[UUID] = Query(None),
    card_id: Optional[UUID] = Query(None),
    user_id: str = Depends(get_user_id)
):
    try:
        # Trigger automatic credit card invoice provisions check
        check_and_generate_invoice_provisions(user_id)
        
        start_iso, end_iso = get_month_boundaries(month)
        
        # Build query
        query = supabase_client.table("transactions").select("*, categories(*)").eq("user_id", user_id).gte("date", start_iso).lte("date", end_iso)
        
        if search and search.strip():
            query = query.ilike("description", f"%{search.strip()}%")
        
        if category_id:
            query = query.eq("category_id", str(category_id))
            
        if card_id:
            query = query.eq("card_id", str(card_id))
            
        response = query.order("date", desc=True).execute()
        
        # Format relationship join keys to match Pydantic model
        result = []
        for item in response.data:
            cat_data = item.get("categories")
            item_copy = dict(item)
            if "categories" in item_copy:
                del item_copy["categories"]
            item_copy["category"] = cat_data
            result.append(item_copy)
            
        return result
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


def add_months(sourcedate, months):
    month = sourcedate.month - 1 + months
    year = sourcedate.year + month // 12
    month = month % 12 + 1
    day = min(sourcedate.day, calendar.monthrange(year, month)[1])
    return datetime(year, month, day, sourcedate.hour, sourcedate.minute, sourcedate.second, sourcedate.microsecond, tzinfo=sourcedate.tzinfo)


def sum_card_activity(transactions: list[dict]) -> int:
    """Card debits increase the bill; credits/refunds reduce it."""
    return sum(
        transaction.get("amount", 0) if transaction.get("type", 0) == 0
        else -transaction.get("amount", 0)
        for transaction in transactions
    )

@app.post("/transactions", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
def create_transaction(transaction: TransactionCreate, user_id: str = Depends(get_user_id)):
    try:
        # Validate that category exists and belongs to the user
        cat_check = supabase_client.table("categories").select("*").eq("id", str(transaction.category_id)).eq("user_id", user_id).execute()
        if not cat_check.data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Category not found or does not belong to the user."
            )
            
        category_data = cat_check.data[0]
        
        # Ensure UTC date storage
        utc_date = transaction.date.astimezone(timezone.utc)
        
        installments = transaction.installments or 1
        is_fixed = transaction.is_fixed or False
        fixed_months = transaction.fixed_months or 1
        
        # Validation for credit card and cash installments
        if transaction.payment_method == "dinheiro" and installments > 1:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Compras com o saldo em dinheiro devem ser sempre à vista."
            )
        if installments > 1 and transaction.payment_method != "cartao":
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Quando for parcelamento, o método de pagamento só pode ser cartão."
            )
            
        card_id = None
        card_data = None
        if transaction.payment_method == "cartao":
            if not transaction.card_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Compras no cartão precisam especificar um cartão de crédito."
                )
            card_check = supabase_client.table("credit_cards").select("*").eq("id", str(transaction.card_id)).eq("user_id", user_id).execute()
            if not card_check.data:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cartão de crédito não encontrado."
                )
            card_data = card_check.data[0]
            card_id = str(transaction.card_id)

        payloads = []
        
        if is_fixed and fixed_months > 1:
            for i in range(fixed_months):
                inst_date = add_months(utc_date, i)
                if transaction.fixed_months:
                    desc_suffix = f" (Fixo {i+1}/{fixed_months})"
                else:
                    desc_suffix = " (Recorrente)"
                inst_desc = (transaction.description or "") + desc_suffix if transaction.description else f"Despesa Fixa{desc_suffix}"
                
                # Future transactions are marked as provisions, current is not (unless requested)
                inst_is_provision = transaction.is_provision
                if i > 0:
                    inst_is_provision = True
                
                inst_invoice_period = None
                if transaction.payment_method == "cartao" and card_data:
                    inst_invoice_period = calculate_invoice_period(inst_date, card_data["closing_day"])
                    
                payloads.append({
                    "amount": transaction.amount,
                    "type": transaction.type,
                    "category_id": str(transaction.category_id),
                    "description": inst_desc,
                    "date": inst_date.isoformat(),
                    "user_id": user_id,
                    "is_provision": inst_is_provision,
                    "payment_method": transaction.payment_method,
                    "card_id": card_id,
                    "invoice_period": inst_invoice_period
                })
        elif installments > 1:
            base_amount = transaction.amount // installments
            remainder = transaction.amount % installments
            
            for i in range(installments):
                inst_date = add_months(utc_date, i)
                desc_suffix = f" ({i+1}/{installments})"
                inst_desc = (transaction.description or "") + desc_suffix if transaction.description else f"Parcela {desc_suffix}"
                inst_amount = base_amount + remainder if i == 0 else base_amount
                
                inst_invoice_period = None
                if transaction.payment_method == "cartao" and card_data:
                    inst_invoice_period = calculate_invoice_period(inst_date, card_data["closing_day"])
                
                payloads.append({
                    "amount": inst_amount,
                    "type": transaction.type,
                    "category_id": str(transaction.category_id),
                    "description": inst_desc,
                    "date": inst_date.isoformat(),
                    "user_id": user_id,
                    "is_provision": transaction.is_provision,
                    "payment_method": transaction.payment_method,
                    "card_id": card_id,
                    "invoice_period": inst_invoice_period
                })
        else:
            inst_invoice_period = None
            if transaction.payment_method == "cartao" and card_data:
                inst_invoice_period = calculate_invoice_period(utc_date, card_data["closing_day"])
                
            payloads.append({
                "amount": transaction.amount,
                "type": transaction.type,
                "category_id": str(transaction.category_id),
                "description": transaction.description,
                "date": utc_date.isoformat(),
                "user_id": user_id,
                "is_provision": transaction.is_provision,
                "payment_method": transaction.payment_method,
                "card_id": card_id,
                "invoice_period": inst_invoice_period
            })
            
        response = supabase_client.table("transactions").insert(payloads).execute()
        created_tx = response.data[0]
        created_tx["category"] = category_data
        
        return created_tx
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.put("/transactions/{id}", response_model=TransactionResponse)
def update_transaction(id: UUID, transaction: TransactionUpdate, user_id: str = Depends(get_user_id)):
    try:
        # Check transaction existence and user ownership
        tx_check = supabase_client.table("transactions").select("*").eq("id", str(id)).eq("user_id", user_id).execute()
        if not tx_check.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found.")
            
        # Check category existence and user ownership
        cat_check = supabase_client.table("categories").select("*").eq("id", str(transaction.category_id)).eq("user_id", user_id).execute()
        if not cat_check.data:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Category not found or does not belong to the user."
            )
            
        category_data = cat_check.data[0]
        
        # Ensure UTC date storage
        utc_date = transaction.date.astimezone(timezone.utc)
        
        card_id = None
        invoice_period = None
        if transaction.payment_method == "cartao":
            if not transaction.card_id:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Compras no cartão precisam especificar um cartão de crédito."
                )
            card_check = supabase_client.table("credit_cards").select("*").eq("id", str(transaction.card_id)).eq("user_id", user_id).execute()
            if not card_check.data:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Cartão de crédito não encontrado."
                )
            card_data = card_check.data[0]
            card_id = str(transaction.card_id)
            invoice_period = calculate_invoice_period(utc_date, card_data["closing_day"])
            
        payload = {
            "amount": transaction.amount,
            "type": transaction.type,
            "category_id": str(transaction.category_id),
            "description": transaction.description,
            "date": utc_date.isoformat(),
            "is_provision": transaction.is_provision,
            "payment_method": transaction.payment_method,
            "card_id": card_id,
            "invoice_period": invoice_period,
            "user_edited": True
        }
        
        response = supabase_client.table("transactions").update(payload).eq("id", str(id)).execute()
        updated_tx = response.data[0]
        updated_tx["category"] = category_data
        
        return updated_tx
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.delete("/transactions/{id}")
def delete_transaction(id: UUID, user_id: str = Depends(get_user_id)):
    try:
        # Validate existence
        tx_check = supabase_client.table("transactions").select("id").eq("id", str(id)).eq("user_id", user_id).execute()
        if not tx_check.data:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found.")
            
        supabase_client.table("transactions").delete().eq("id", str(id)).execute()
        return {"message": "Transaction deleted successfully."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# Helper function to generate summary calculations
def calculate_summary(user_id: str, month_str: str) -> dict:
    start_iso, end_iso = get_month_boundaries(month_str)
    
    # 1. Retrieve all transactions in the history up to end_iso (to compute cumulative balance)
    response_all = supabase_client.table("transactions")\
        .select("amount, type, is_provision, payment_method")\
        .eq("user_id", user_id)\
        .lte("date", end_iso)\
        .execute()
        
    all_txs = response_all.data
    cumulative_income = 0
    cumulative_cash_expense = 0
    
    for tx in all_txs:
        is_provision = tx.get("is_provision", False)
        if not is_provision:
            amount = tx.get("amount", 0)
            tx_type = tx.get("type", 0)
            payment_method = tx.get("payment_method", "dinheiro")
            
            if tx_type == 1 and payment_method != "cartao":
                cumulative_income += amount
            else:
                if payment_method == "dinheiro":
                    cumulative_cash_expense += amount
                    
    balance = cumulative_income - cumulative_cash_expense

    # For the current month, the institution balance is more accurate than a
    # sum of imported history (Pluggy initially returns a limited lookback).
    if month_str == datetime.now(timezone.utc).strftime("%Y-%m"):
        account_balances = (
            supabase_client.table("financial_accounts")
            .select("balance")
            .eq("user_id", user_id)
            .eq("type", "BANK")
            .execute()
        ).data or []
        if account_balances:
            balance = sum(account.get("balance", 0) for account in account_balances)

    # 2. Retrieve all transactions for the month with their corresponding categories
    response = supabase_client.table("transactions").select("*, categories(*)").eq("user_id", user_id).gte("date", start_iso).lte("date", end_iso).execute()
    transactions = response.data
    
    total_income = 0
    total_expense = 0
    total_card_expense = 0
    total_provisions_income = 0
    total_provisions_expense = 0
    category_totals = {}  # category_id -> dict
    
    for tx in transactions:
        amount = tx.get("amount", 0)
        tx_type = tx.get("type", 0)
        is_provision = tx.get("is_provision", False)
        payment_method = tx.get("payment_method", "dinheiro")
        
        if is_provision:
            if tx_type == 1:
                total_provisions_income += amount
            else:
                total_provisions_expense += amount
        else:
            if tx_type == 1:
                if payment_method == "cartao":
                    total_card_expense -= amount
                else:
                    total_income += amount
            else:
                card_id = tx.get("card_id")
                
                # 1. Cash flow totals
                if payment_method == "cartao":
                    total_card_expense += amount
                else:
                    total_expense += amount
                
                # 2. Consumption totals for categories and budgets
                # Include cash purchases (card_id is None) and card purchases (payment_method == "cartao").
                # Exclude invoice payments (payment_method == "dinheiro" and card_id is not null) or manual 'Crédito' payments.
                cat = tx.get("categories")
                cat_name = cat.get("name") if cat else ""
                
                is_invoice_payment = (
                    (payment_method == "dinheiro" and card_id is not None) or
                    (cat_name.lower() in ("crédito", "credito"))
                )
                
                if not is_invoice_payment:
                    if cat:
                        cat_id = cat.get("id")
                        if cat_id not in category_totals:
                            category_totals[cat_id] = {
                                "category_name": cat.get("name"),
                                "icon": cat.get("icon"),
                                "total": 0
                            }
                        category_totals[cat_id]["total"] += amount
                    
    # Calculate total consumption (sum of all categorized expenses including cash & card)
    total_consumption = sum(data["total"] for data in category_totals.values())
    
    breakdown = []
    for cat_id, data in category_totals.items():
        percentage = (data["total"] / total_consumption * 100.0) if total_consumption > 0 else 0.0
        breakdown.append({
            "category_name": data["category_name"],
            "icon": data["icon"],
            "total": data["total"],
            "percentage": round(percentage, 2)
        })
        
    # Sort breakdown in descending order of total expenses
    breakdown.sort(key=lambda x: x["total"], reverse=True)
    
    # 3. Calculate provisions and realized expenses for next month
    try:
        ref_date = datetime.strptime(month_str, "%Y-%m")
        next_month_date = add_months(ref_date, 1)
        next_month_str = next_month_date.strftime("%Y-%m")
    except Exception:
        next_month_date = datetime.now() + timedelta(days=30)
        next_month_str = next_month_date.strftime("%Y-%m")
        
    next_start_iso, next_end_iso = get_month_boundaries(next_month_str)
    
    response_next = supabase_client.table("transactions")\
        .select("amount, type, is_provision, payment_method, card_id")\
        .eq("user_id", user_id)\
        .eq("type", 0)\
        .gte("date", next_start_iso)\
        .lte("date", next_end_iso)\
        .execute()
        
    next_txs = response_next.data or []
    # Exclude card provisions (card_id is not null)
    next_month_provisions_expense = sum(
        t.get("amount", 0) for t in next_txs 
        if t.get("is_provision", False) and t.get("card_id") is None
    )
    # Exclude card purchases (payment_method == "cartao")
    next_month_realized_expense = sum(
        t.get("amount", 0) for t in next_txs 
        if not t.get("is_provision", False) and t.get("payment_method") != "cartao"
    )
    
    # Calculate next month's credit card invoices due
    next_month_card_liability = 0
    try:
        cards_response = supabase_client.table("credit_cards").select("id, name, closing_day, due_day").eq("user_id", user_id).execute()
        cards = cards_response.data or []
        for card in cards:
            card_id = card["id"]
            closing_day = card["closing_day"]
            due_day = card["due_day"]
            
            # Determine invoice period due in next month
            if due_day > closing_day:
                due_period = next_month_str
            else:
                due_period = month_str
                
            # Check for existing invoice provision
            tx_prov_check = supabase_client.table("transactions")\
                .select("amount")\
                .eq("user_id", user_id)\
                .eq("card_id", card_id)\
                .eq("is_provision", True)\
                .eq("invoice_period", due_period)\
                .execute()
                
            if tx_prov_check.data:
                invoice_amount = sum(t["amount"] for t in tx_prov_check.data)
            else:
                # Sum card purchases for this period
                tx_purchases = supabase_client.table("transactions")\
                    .select("amount, type")\
                    .eq("user_id", user_id)\
                    .eq("card_id", card_id)\
                    .eq("payment_method", "cartao")\
                    .eq("is_provision", False)\
                    .eq("invoice_period", due_period)\
                    .execute()
                invoice_amount = sum_card_activity(tx_purchases.data)
                
            next_month_card_liability += invoice_amount
    except Exception as e:
        print(f"Error calculating card liabilities for next month: {str(e)}")
        
    # 4. Calculate budget progress for the selected month
    budgets_progress = []
    try:
        budgets_res = supabase_client.table("budgets").select("*").eq("user_id", user_id).eq("period", month_str).execute()
        budgets_list = budgets_res.data or []
        
        if budgets_list:
            cats_res = supabase_client.table("categories").select("id, name").eq("user_id", user_id).eq("type", 0).execute()
            cats_map = {c["id"]: c["name"] for c in cats_res.data or []}
            spent_by_name = {item["category_name"]: item["total"] for item in breakdown}
            
            for b in budgets_list:
                spent_amount = 0
                for cat_id in b.get("category_ids", []):
                    cat_name = cats_map.get(cat_id)
                    if cat_name:
                        spent_amount += spent_by_name.get(cat_name, 0)
                        
                percentage = round((spent_amount / b["amount"]) * 100.0, 2) if b["amount"] > 0 else 0.0
                budgets_progress.append({
                    "name": b["name"],
                    "amount": b["amount"],
                    "spent_amount": spent_amount,
                    "percentage": percentage
                })
    except Exception as e:
        print(f"Error calculating budgets progress: {str(e)}")
        
    return {
        "total_income": total_income,
        "total_expense": total_expense,
        "balance": balance,
        "total_card_expense": total_card_expense,
        "total_provisions_income": total_provisions_income,
        "total_provisions_expense": total_provisions_expense,
        "next_month_provisions_expense": next_month_provisions_expense,
        "next_month_realized_expense": next_month_realized_expense,
        "next_month_card_liability": next_month_card_liability,
        "category_breakdown": breakdown,
        "budgets": budgets_progress
    }


# --- SUMMARY ENDPOINTS ---

@app.get("/summary", response_model=SummaryResponse)
def get_summary(
    month: Optional[str] = Query(None, description="Format 'YYYY-MM', defaults to current month"),
    user_id: str = Depends(get_user_id)
):
    try:
        # Trigger automatic credit card invoice provisions check
        check_and_generate_invoice_provisions(user_id)
        
        # Default to current month if parameter not provided
        month_val = month if month else datetime.now(timezone.utc).strftime("%Y-%m")
        return calculate_summary(user_id, month_val)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.get("/summary/top-categories", response_model=List[CategoryBreakdownItem])
def get_top_categories(
    month: Optional[str] = Query(None, description="Format 'YYYY-MM', defaults to current month"),
    user_id: str = Depends(get_user_id)
):
    try:
        month_val = month if month else datetime.now(timezone.utc).strftime("%Y-%m")
        summary = calculate_summary(user_id, month_val)
        # Return only the top 3 highest expense categories
        return summary["category_breakdown"][:3]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


@app.get("/summary/trend", response_model=MonthlyTrendResponse)
def get_monthly_trend(
    month: Optional[str] = Query(None, description="Format 'YYYY-MM', defaults to current month"),
    user_id: str = Depends(get_user_id)
):
    try:
        ref_month_str = month if month else datetime.now(timezone.utc).strftime("%Y-%m")
        ref_date = datetime.strptime(ref_month_str, "%Y-%m")
        
        months = []
        for i in range(-3, 4):
            m_date = add_months(ref_date, i)
            months.append(m_date.strftime("%Y-%m"))
            
        start_month_str = months[0]
        end_month_str = months[-1]
        
        start_iso, _ = get_month_boundaries(start_month_str)
        _, end_iso = get_month_boundaries(end_month_str)
        
        tx_res = supabase_client.table("transactions")\
            .select("amount, type, date, payment_method, is_provision, card_id, categories(name)")\
            .eq("user_id", user_id)\
            .gte("date", start_iso)\
            .lte("date", end_iso)\
            .execute()
            
        txs = tx_res.data or []
        
        monthly_data = {m: {"income": 0, "expense": 0, "card_expense": 0} for m in months}
        
        for tx in txs:
            date_str = tx.get("date")
            if not date_str:
                continue
            
            try:
                tx_month = date_str[:7]
            except Exception:
                continue
                
            if tx_month in monthly_data:
                amount = tx.get("amount", 0)
                tx_type = tx.get("type", 0)
                payment_method = tx.get("payment_method", "dinheiro")
                
                if tx_type == 1:
                    if payment_method == "cartao":
                        monthly_data[tx_month]["card_expense"] -= amount
                    else:
                        monthly_data[tx_month]["income"] += amount
                else:
                    if payment_method == "cartao":
                        monthly_data[tx_month]["card_expense"] += amount
                    else:
                        monthly_data[tx_month]["expense"] += amount
                            
        trend_items = []
        for m in months:
            trend_items.append({
                "month": m,
                "income": monthly_data[m]["income"],
                "expense": monthly_data[m]["expense"],
                "card_expense": monthly_data[m]["card_expense"]
            })
            
        return {"trend": trend_items}
        
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(e))


# --- CREDIT CARD ENDPOINTS ---

@app.get("/credit-cards", response_model=List[CreditCardSummaryResponse])
def list_credit_cards(user_id: str = Depends(get_user_id)):
    try:
        check_and_generate_invoice_provisions(user_id)
        
        res = supabase_client.table("credit_cards").select("*").eq("user_id", user_id).execute()
        cards = res.data
        
        current_time = datetime.now(timezone.utc)
        result = []
        
        for card in cards:
            card_id = card["id"]
            limit = card["limit"]
            closing_day = card["closing_day"]
            due_day = card["due_day"]
            
            active_period, cycle_start, cycle_end = get_active_invoice_cycle(card, current_time)
            
            tx_active = supabase_client.table("transactions").select("amount, type")\
                .eq("user_id", user_id)\
                .eq("card_id", card_id)\
                .eq("payment_method", "cartao")\
                .eq("is_provision", False)\
                .eq("invoice_period", active_period)\
                .execute()
            current_invoice_amount = sum_card_activity(tx_active.data)
            
            paid_txs = supabase_client.table("transactions")\
                .select("invoice_period")\
                .eq("user_id", user_id)\
                .eq("card_id", card_id)\
                .eq("payment_method", "dinheiro")\
                .eq("is_provision", False)\
                .not_.is_("invoice_period", "null")\
                .execute()
            paid_periods = {t["invoice_period"] for t in paid_txs.data}
            
            tx_all = supabase_client.table("transactions").select("amount, type, invoice_period")\
                .eq("user_id", user_id)\
                .eq("card_id", card_id)\
                .eq("payment_method", "cartao")\
                .eq("is_provision", False)\
                .execute()
                
            unpaid_amount = sum_card_activity(
                [t for t in tx_all.data if t.get("invoice_period") not in paid_periods]
            )
            available_limit = max(0, limit - unpaid_amount)

            if card.get("source") == "pluggy" and card.get("pluggy_account_id"):
                account = (
                    supabase_client.table("financial_accounts")
                    .select("balance")
                    .eq("user_id", user_id)
                    .eq("pluggy_account_id", card["pluggy_account_id"])
                    .limit(1)
                    .execute()
                )
                if account.data:
                    current_invoice_amount = max(0, account.data[0].get("balance", 0))
                if card.get("available_limit") is not None:
                    available_limit = max(0, card["available_limit"])
            
            result.append({
                "id": card_id,
                "name": card["name"],
                "limit": limit,
                "closing_day": closing_day,
                "due_day": due_day,
                "current_invoice_amount": current_invoice_amount,
                "available_limit": available_limit,
                "source": card.get("source", "manual")
            })
            
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/credit-cards", response_model=CreditCardResponse, status_code=status.HTTP_201_CREATED)
def create_credit_card(card: CreditCardCreate, user_id: str = Depends(get_user_id)):
    try:
        payload = {
            "name": card.name,
            "limit": card.limit,
            "closing_day": card.closing_day,
            "due_day": card.due_day,
            "user_id": user_id
        }
        res = supabase_client.table("credit_cards").insert(payload).execute()
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.put("/credit-cards/{id}", response_model=CreditCardResponse)
def update_credit_card(id: UUID, card: CreditCardUpdate, user_id: str = Depends(get_user_id)):
    try:
        check = supabase_client.table("credit_cards").select("id").eq("id", str(id)).eq("user_id", user_id).execute()
        if not check.data:
            raise HTTPException(status_code=404, detail="Credit card not found.")
            
        payload = {
            "name": card.name,
            "limit": card.limit,
            "closing_day": card.closing_day,
            "due_day": card.due_day
        }
        res = supabase_client.table("credit_cards").update(payload).eq("id", str(id)).execute()
        return res.data[0]
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/credit-cards/{id}")
def delete_credit_card(id: UUID, user_id: str = Depends(get_user_id)):
    try:
        check = supabase_client.table("credit_cards").select("id").eq("id", str(id)).eq("user_id", user_id).execute()
        if not check.data:
            raise HTTPException(status_code=404, detail="Credit card not found.")
            
        supabase_client.table("credit_cards").delete().eq("id", str(id)).execute()
        return {"message": "Credit card deleted successfully."}
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- BUDGET ENDPOINTS (METAS DE GASTOS) ---

@app.get("/budgets", response_model=List[BudgetResponse])
def list_budgets(
    period: str = Query(..., description="Format: YYYY-MM"),
    user_id: str = Depends(get_user_id)
):
    try:
        res = supabase_client.table("budgets").select("*").eq("user_id", user_id).eq("period", period).execute()
        return res.data or []
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/budgets", response_model=BudgetResponse, status_code=status.HTTP_201_CREATED)
def create_or_update_budget(
    budget: BudgetCreate,
    user_id: str = Depends(get_user_id)
):
    try:
        res_check = supabase_client.table("budgets")\
            .select("id")\
            .eq("user_id", user_id)\
            .eq("name", budget.name)\
            .eq("period", budget.period)\
            .execute()
            
        payload = {
            "name": budget.name,
            "amount": budget.amount,
            "period": budget.period,
            "category_ids": [str(c_id) for c_id in budget.category_ids],
            "user_id": user_id
        }
        
        if res_check.data:
            budget_id = res_check.data[0]["id"]
            res = supabase_client.table("budgets").update(payload).eq("id", budget_id).execute()
        else:
            res = supabase_client.table("budgets").insert(payload).execute()
            
        return res.data[0]
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/budgets/{id}")
def delete_budget(
    id: UUID,
    user_id: str = Depends(get_user_id)
):
    try:
        supabase_client.table("budgets").delete().eq("id", str(id)).eq("user_id", user_id).execute()
        return {"message": "Budget deleted successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# --- PLUGGY / OPEN FINANCE ENDPOINTS ---

def _connection_for_item(item_id: str, user_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    query = supabase_client.table("pluggy_connections").select("*").eq("item_id", item_id)
    if user_id:
        query = query.eq("user_id", user_id)
    response = query.limit(1).execute()
    return response.data[0] if response.data else None


def _sync_connection_in_background(user_id: str, item_id: str, trigger_update: bool) -> None:
    try:
        client = get_pluggy_client()
        if trigger_update:
            try:
                client.update_item(item_id)
            except PluggyError as error:
                # A running sync/frequency limit must not prevent importing the
                # latest data already available at Pluggy.
                if error.status_code not in (400, 409):
                    raise
        reconcile_item(client, user_id, item_id)
    except Exception as error:
        supabase_client.table("pluggy_connections").update(
            {
                "last_error": str(error)[:500],
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }
        ).eq("item_id", item_id).eq("user_id", user_id).execute()


@app.post("/pluggy/connect-token", response_model=PluggyConnectTokenResponse)
def create_pluggy_connect_token(
    request: PluggyConnectTokenRequest,
    user_id: str = Depends(get_user_id),
):
    if request.item_id and not _connection_for_item(request.item_id, user_id):
        raise HTTPException(status_code=404, detail="Conexão financeira não encontrada.")
    try:
        token = get_pluggy_client().create_connect_token(
            user_id=user_id,
            webhook_url=pluggy_webhook_url(),
            item_id=request.item_id,
        )
        return {
            "access_token": token["accessToken"],
            "include_sandbox": settings.pluggy_include_sandbox,
        }
    except PluggyError as error:
        raise_pluggy_http_error(error)


@app.post("/pluggy/items", status_code=status.HTTP_202_ACCEPTED)
def register_pluggy_item(
    request: PluggyItemRegisterRequest,
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_user_id),
):
    try:
        item = get_pluggy_client().get_item(request.item_id)
        if item.get("clientUserId") != user_id:
            raise HTTPException(status_code=403, detail="Esta conexão não pertence ao usuário atual.")
        upsert_connection(user_id, item)
        background_tasks.add_task(_sync_connection_in_background, user_id, request.item_id, False)
        return {"message": "Conexão recebida. A importação foi iniciada."}
    except PluggyError as error:
        raise_pluggy_http_error(error)


@app.get("/pluggy/connections", response_model=List[PluggyConnectionResponse])
def list_pluggy_connections(user_id: str = Depends(get_user_id)):
    response = (
        supabase_client.table("pluggy_connections")
        .select(
            "item_id, connector_name, connector_image_url, status, execution_status, "
            "last_successful_update_at, next_auto_sync_at, last_sync_at, last_error"
        )
        .eq("user_id", user_id)
        .order("created_at")
        .execute()
    )
    return response.data or []


@app.post("/pluggy/sync", response_model=PluggySyncResponse, status_code=status.HTTP_202_ACCEPTED)
def sync_pluggy_connections(
    background_tasks: BackgroundTasks,
    user_id: str = Depends(get_user_id),
):
    connections = (
        supabase_client.table("pluggy_connections")
        .select("item_id")
        .eq("user_id", user_id)
        .execute()
    ).data or []
    background_tasks.add_task(_retry_pending_pluggy_webhooks)
    for connection in connections:
        background_tasks.add_task(
            _sync_connection_in_background,
            user_id,
            connection["item_id"],
            True,
        )
    return {
        "message": "Atualização iniciada. A Pluggy avisará quando os novos dados estiverem prontos.",
        "connections_queued": len(connections),
    }


@app.delete("/pluggy/connections/{item_id}")
def delete_pluggy_connection(item_id: str, user_id: str = Depends(get_user_id)):
    if not _connection_for_item(item_id, user_id):
        raise HTTPException(status_code=404, detail="Conexão financeira não encontrada.")
    try:
        get_pluggy_client().delete_item(item_id)
        delete_imported_item_data(user_id, item_id)
        return {"message": "Conexão e dados importados removidos."}
    except PluggyError as error:
        if error.status_code == 404:
            delete_imported_item_data(user_id, item_id)
            return {"message": "Dados locais da conexão removidos."}
        raise_pluggy_http_error(error)


def _process_pluggy_webhook(payload: dict[str, Any]) -> None:
    event_id = payload.get("eventId")
    item_id = payload.get("itemId")
    event = payload.get("event", "")
    try:
        client = get_pluggy_client()
        connection = _connection_for_item(item_id) if item_id else None
        user_id = connection.get("user_id") if connection else payload.get("clientUserId")

        if event == "transactions/deleted" and not user_id:
            transaction_ids = payload.get("transactionIds") or []
            if transaction_ids:
                # This event may omit itemId/clientUserId. Pluggy transaction
                # ids are global, so the source id safely resolves local rows.
                supabase_client.table("transactions").delete().in_(
                    "pluggy_transaction_id", transaction_ids
                ).execute()
            if event_id:
                supabase_client.table("pluggy_webhook_events").update(
                    {
                        "status": "processed",
                        "processed_at": datetime.now(timezone.utc).isoformat(),
                        "last_error": None,
                    }
                ).eq("event_id", event_id).execute()
            return

        if not user_id:
            raise PluggyError("Webhook sem usuário associado.", 422)

        if event in ("item/created", "item/updated") and item_id:
            item = client.get_item(item_id)
            if item.get("clientUserId") != user_id:
                raise PluggyError("O item recebido não pertence ao usuário informado.", 403)
            upsert_connection(user_id, item)
            if event == "item/created":
                # Guarantees the initial history even if a transaction event was
                # delayed or missed. Later syncs are incremental via webhooks.
                reconcile_item(client, user_id, item_id)
            else:
                sync_accounts(client, user_id, item_id)

        elif event == "item/error" and item_id:
            error_data = payload.get("error") or {}
            message = error_data.get("message") or error_data.get("code") or "Erro ao atualizar conexão."
            supabase_client.table("pluggy_connections").update(
                {"status": "ERROR", "last_error": str(message)[:500]}
            ).eq("item_id", item_id).eq("user_id", user_id).execute()

        elif event == "item/deleted" and item_id:
            delete_imported_item_data(user_id, item_id)

        elif event == "transactions/created" and item_id:
            account_id = payload.get("accountId")
            if not account_id:
                raise PluggyError("Webhook de transação sem conta associada.", 422)
            # Prefer the V2 link introduced for current applications.
            transaction_link = payload.get("createdTransactionsLinkV2") or payload.get("createdTransactionsLink")
            transactions = client.get_transactions_from_link(transaction_link) if transaction_link else []
            if not transactions:
                transactions = client.get_all_transactions(account_id)
            if not connection:
                sync_accounts(client, user_id, item_id)
            sync_transactions(user_id, item_id, account_id, transactions)

        elif event == "transactions/updated" and item_id:
            account_id = payload.get("accountId")
            transaction_ids = payload.get("transactionIds") or []
            if account_id and transaction_ids:
                page = client.get_transaction_page(
                    account_id,
                    transaction_ids=transaction_ids,
                )
                sync_transactions(user_id, item_id, account_id, page.get("results", []))

        elif event == "transactions/deleted":
            transaction_ids = payload.get("transactionIds") or []
            if transaction_ids:
                supabase_client.table("transactions").delete().eq("user_id", user_id).in_(
                    "pluggy_transaction_id", transaction_ids
                ).execute()

        if event_id:
            supabase_client.table("pluggy_webhook_events").update(
                {
                    "status": "processed",
                    "processed_at": datetime.now(timezone.utc).isoformat(),
                    "last_error": None,
                }
            ).eq("event_id", event_id).execute()
    except Exception as error:
        if event_id:
            supabase_client.table("pluggy_webhook_events").update(
                {"status": "failed", "last_error": str(error)[:1000]}
            ).eq("event_id", event_id).execute()


def _retry_pending_pluggy_webhooks() -> None:
    events = (
        supabase_client.table("pluggy_webhook_events")
        .select("payload")
        .in_("status", ["pending", "failed"])
        .order("received_at")
        .limit(20)
        .execute()
    ).data or []
    for event in events:
        if isinstance(event.get("payload"), dict):
            _process_pluggy_webhook(event["payload"])


@app.post("/webhooks/pluggy", status_code=status.HTTP_202_ACCEPTED)
def receive_pluggy_webhook(
    background_tasks: BackgroundTasks,
    payload: dict[str, Any] = Body(...),
    token: Optional[str] = Query(None),
    x_pluggy_webhook_secret: Optional[str] = Header(None),
):
    provided_secret = x_pluggy_webhook_secret or token or ""
    if not settings.pluggy_webhook_secret or not secrets.compare_digest(
        provided_secret, settings.pluggy_webhook_secret
    ):
        raise HTTPException(status_code=401, detail="Webhook não autorizado.")

    event_id = payload.get("eventId")
    event_name = payload.get("event")
    if not event_id or not event_name:
        raise HTTPException(status_code=400, detail="Webhook inválido.")

    existing = (
        supabase_client.table("pluggy_webhook_events")
        .select("event_id, status, payload")
        .eq("event_id", event_id)
        .limit(1)
        .execute()
    )
    if existing.data:
        if existing.data[0].get("status") != "processed":
            background_tasks.add_task(_process_pluggy_webhook, existing.data[0]["payload"])
        return {"received": True, "duplicate": True}

    supabase_client.table("pluggy_webhook_events").insert(
        {
            "event_id": event_id,
            "event_name": event_name,
            "item_id": payload.get("itemId"),
            "payload": payload,
            "status": "pending",
        }
    ).execute()
    background_tasks.add_task(_process_pluggy_webhook, payload)
    return {"received": True}
