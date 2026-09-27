"""API for the current Supabase finance schema, shared by web and native clients."""

from datetime import datetime, timezone
from typing import Optional
from uuid import UUID

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

from app.config import settings
from app.current_sync import sync_all
from app.database import supabase_client
from app.mobile import _rows, _user_id, overview, router as mobile_router
from app.pluggy import PluggyClient, PluggyError

app = FastAPI(title="MeuFinanças API", version="2.0.0")
app.include_router(mobile_router)


def _client() -> PluggyClient:
    if not settings.pluggy_client_id or not settings.pluggy_client_secret:
        raise HTTPException(status_code=503, detail="Pluggy não configurada no servidor.")
    return PluggyClient(settings.pluggy_client_id, settings.pluggy_client_secret)


@app.get("/health")
def health():
    if not settings.supabase_service_role_key or not settings.app_api_token:
        raise HTTPException(status_code=503, detail="Configure SUPABASE_SERVICE_ROLE_KEY e APP_API_TOKEN no servidor.")
    return {"status": "ok", "schema": "current"}


@app.get("/categories")
def categories(user_id: str = Depends(_user_id)):
    return sorted(_rows("categories", user_id), key=lambda row: row.get("name") or "")


class CategoryInput(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    type: str
    icon: str = "•"


@app.post("/categories", status_code=201)
def create_category(data: CategoryInput, user_id: str = Depends(_user_id)):
    if data.type not in ("income", "expense"):
        raise HTTPException(status_code=422, detail="Tipo de categoria inválido.")
    name = data.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Informe o nome da categoria.")
    result = supabase_client.table("categories").insert({
        "user_id": user_id, "name": name, "type": data.type, "icon": data.icon,
    }).execute()
    return result.data[0]


@app.get("/transactions")
def transactions(month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
                 user_id: str = Depends(_user_id)):
    from app.mobile import _month_transactions
    return sorted(_month_transactions(user_id, month),
                  key=lambda row: (row.get("transaction_date") or "", row["id"]), reverse=True)


class TransactionEdit(BaseModel):
    description: Optional[str] = Field(default=None, max_length=255)
    category_id: Optional[UUID] = None


@app.put("/transactions/{transaction_id}")
def update_transaction(transaction_id: UUID, data: TransactionEdit, user_id: str = Depends(_user_id)):
    payload = data.model_dump(exclude_unset=True)
    if "category_id" in payload:
        category_id = payload["category_id"]
        if category_id is not None:
            category = (supabase_client.table("categories").select("id")
                        .eq("id", str(category_id)).eq("user_id", user_id).execute().data or [])
            if not category:
                raise HTTPException(status_code=404, detail="Categoria não encontrada.")
        payload["category_id"] = str(category_id) if category_id else None
        payload["user_edited_category"] = True
    if "description" in payload:
        payload["description"] = (payload["description"] or "").strip()
        if not payload["description"]:
            raise HTTPException(status_code=422, detail="Informe uma descrição.")
        payload["user_edited_description"] = True
    if not payload:
        raise HTTPException(status_code=422, detail="Nenhuma alteração enviada.")
    payload["updated_at"] = datetime.now(timezone.utc).isoformat()
    result = (supabase_client.table("transactions").update(payload)
              .eq("id", str(transaction_id)).eq("user_id", user_id).execute().data or [])
    if not result:
        raise HTTPException(status_code=404, detail="Lançamento não encontrado.")
    return result[0]


@app.get("/financial-accounts")
def financial_accounts(user_id: str = Depends(_user_id)):
    return _rows("financial_accounts", user_id)


@app.get("/investments")
def investments(user_id: str = Depends(_user_id)):
    return _rows("investments", user_id)


@app.get("/credit-cards")
def credit_cards(user_id: str = Depends(_user_id)):
    return _rows("credit_cards", user_id)


class CardDates(BaseModel):
    closing_day: int = Field(ge=1, le=31)
    due_day: int = Field(ge=1, le=31)


@app.put("/credit-cards/{card_id}")
def update_card_dates(card_id: UUID, data: CardDates, user_id: str = Depends(_user_id)):
    result = (supabase_client.table("credit_cards").update(data.model_dump())
              .eq("id", str(card_id)).eq("user_id", user_id).execute().data or [])
    if not result:
        raise HTTPException(status_code=404, detail="Cartão não encontrado.")
    return result[0]


@app.get("/summary")
def summary(month: str = Query(..., pattern=r"^\d{4}-(0[1-9]|1[0-2])$"),
            user_id: str = Depends(_user_id)):
    return {"month": month, **overview(month=month, user_id=user_id)["summary"]}


@app.get("/pluggy/connections")
def connections(user_id: str = Depends(_user_id)):
    return _rows("pluggy_connections", user_id)


class ItemInput(BaseModel):
    item_id: UUID
    institution_name: Optional[str] = None


@app.post("/pluggy/items")
def register_item(data: ItemInput, user_id: str = Depends(_user_id)):
    try:
        client = _client()
        item = client.get_item(str(data.item_id))
        if item.get("clientUserId") and item["clientUserId"] != user_id:
            raise HTTPException(status_code=403, detail="Instituição de outro usuário.")
        connector = item.get("connector") or {}
        connection_payload = {
            "user_id": user_id, "item_id": str(data.item_id),
            "institution_name": data.institution_name or connector.get("name") or "Instituição",
            "connector_id": connector.get("id"),
            "connector_image_url": connector.get("imageUrl"),
            "status": item.get("status") or "UNKNOWN", "sync_status": "pending",
            "raw_payload": item,
        }
        existing = (supabase_client.table("pluggy_connections").select("id")
                    .eq("user_id", user_id).eq("item_id", str(data.item_id)).execute().data or [])
        if existing:
            (supabase_client.table("pluggy_connections").update(connection_payload)
             .eq("id", existing[0]["id"]).eq("user_id", user_id).execute())
        else:
            supabase_client.table("pluggy_connections").insert(connection_payload).execute()
        return sync_all(user_id, client)
    except PluggyError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error


@app.post("/pluggy/sync")
@app.post("/mobile/sync")
def sync(user_id: str = Depends(_user_id)):
    try:
        return sync_all(user_id, _client())
    except PluggyError as error:
        raise HTTPException(status_code=error.status_code, detail=str(error)) from error


@app.get("/pluggy/sync/{sync_id}")
def sync_status(sync_id: UUID, user_id: str = Depends(_user_id)):
    rows = (supabase_client.table("pluggy_sync_runs").select("*")
            .eq("id", str(sync_id)).eq("user_id", user_id).execute().data or [])
    if not rows:
        raise HTTPException(status_code=404, detail="Sincronização não encontrada.")
    return rows[0]
