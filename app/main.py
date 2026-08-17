import calendar
import uuid
from uuid import UUID
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
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
                        
                        tx_response = supabase_client.table("transactions").select("amount")\
                            .eq("user_id", user_id)\
                            .eq("card_id", card_id)\
                            .eq("type", 0)\
                            .eq("payment_method", "cartao")\
                            .eq("is_provision", False)\
                            .gte("date", start_cycle.isoformat())\
                            .lte("date", end_cycle.isoformat())\
                            .execute()
                            
                        total_amount = sum(t["amount"] for t in tx_response.data)
                        
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
            "invoice_period": invoice_period
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
            
            if tx_type == 1:
                cumulative_income += amount
            else:
                if payment_method == "dinheiro":
                    cumulative_cash_expense += amount
                    
    balance = cumulative_income - cumulative_cash_expense

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
                total_income += amount
            else:
                total_expense += amount
                if payment_method == "cartao":
                    total_card_expense += amount
                
                cat = tx.get("categories")
                if cat:
                    cat_id = cat.get("id")
                    if cat_id not in category_totals:
                        category_totals[cat_id] = {
                            "category_name": cat.get("name"),
                            "icon": cat.get("icon"),
                            "total": 0
                        }
                    category_totals[cat_id]["total"] += amount
                    
    breakdown = []
    for cat_id, data in category_totals.items():
        percentage = (data["total"] / total_expense * 100.0) if total_expense > 0 else 0.0
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
                    .select("amount")\
                    .eq("user_id", user_id)\
                    .eq("card_id", card_id)\
                    .eq("type", 0)\
                    .eq("payment_method", "cartao")\
                    .eq("is_provision", False)\
                    .eq("invoice_period", due_period)\
                    .execute()
                invoice_amount = sum(t["amount"] for t in tx_purchases.data)
                
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
            
            tx_active = supabase_client.table("transactions").select("amount")\
                .eq("user_id", user_id)\
                .eq("card_id", card_id)\
                .eq("type", 0)\
                .eq("payment_method", "cartao")\
                .eq("is_provision", False)\
                .eq("invoice_period", active_period)\
                .execute()
            current_invoice_amount = sum(t["amount"] for t in tx_active.data)
            
            paid_txs = supabase_client.table("transactions")\
                .select("invoice_period")\
                .eq("user_id", user_id)\
                .eq("card_id", card_id)\
                .eq("payment_method", "dinheiro")\
                .eq("is_provision", False)\
                .not_.is_("invoice_period", "null")\
                .execute()
            paid_periods = {t["invoice_period"] for t in paid_txs.data}
            
            tx_all = supabase_client.table("transactions").select("amount, invoice_period")\
                .eq("user_id", user_id)\
                .eq("card_id", card_id)\
                .eq("type", 0)\
                .eq("payment_method", "cartao")\
                .eq("is_provision", False)\
                .execute()
                
            unpaid_amount = sum(t["amount"] for t in tx_all.data if t.get("invoice_period") not in paid_periods)
            available_limit = max(0, limit - unpaid_amount)
            
            result.append({
                "id": card_id,
                "name": card["name"],
                "limit": limit,
                "closing_day": closing_day,
                "due_day": due_day,
                "current_invoice_amount": current_invoice_amount,
                "available_limit": available_limit
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
