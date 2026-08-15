from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from uuid import UUID

# --- Category Schemas ---

class CategoryBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    icon: str = Field(..., description="Emoji or Material icon name")
    type: int = Field(..., description="0 = Expense/Despesa, 1 = Income/Receita")

class CategoryCreate(CategoryBase):
    pass

class CategoryUpdate(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    icon: str = Field(...)

class CategoryResponse(CategoryBase):
    id: UUID
    is_default: bool
    user_id: UUID
    created_at: Optional[datetime] = None

    class Config:
        from_attributes = True


# --- Transaction Schemas ---

class TransactionBase(BaseModel):
    amount: int = Field(..., gt=0, description="Amount in cents, must be positive")
    type: int = Field(..., description="0 = Expense/Despesa, 1 = Income/Receita")
    category_id: UUID
    description: Optional[str] = Field(None, max_length=255)
    date: datetime = Field(..., description="Date/time of transaction, will be saved in UTC")
    is_provision: bool = Field(False, description="Whether this is a future provision or bill")
    payment_method: str = Field("dinheiro", description="Payment method: 'dinheiro' or 'cartao'")
    card_id: Optional[UUID] = None
    invoice_period: Optional[str] = None

class TransactionCreate(TransactionBase):
    installments: Optional[int] = Field(1, ge=1, description="Number of installments for credit card purchases")
    is_fixed: Optional[bool] = Field(False, description="Whether this is a fixed recurring expense")
    fixed_months: Optional[int] = Field(None, ge=1, description="Number of months for fixed expense period")

class TransactionUpdate(TransactionBase):
    pass

# Transaction with nested category details for response
class TransactionResponse(BaseModel):
    id: UUID
    amount: int
    type: int
    category_id: UUID
    description: Optional[str]
    date: datetime
    created_at: datetime
    user_id: UUID
    is_provision: bool
    payment_method: str = "dinheiro"
    card_id: Optional[UUID] = None
    invoice_period: Optional[str] = None
    category: Optional[CategoryResponse] = None

    class Config:
        from_attributes = True


# --- Summary Schemas ---

class CategoryBreakdownItem(BaseModel):
    category_name: str
    icon: str
    total: int
    percentage: float

class SummaryResponse(BaseModel):
    total_income: int
    total_expense: int
    balance: int
    total_card_expense: int = 0
    total_provisions_expense: int = 0
    total_provisions_income: int = 0
    category_breakdown: List[CategoryBreakdownItem]


# --- Credit Card Schemas ---

class CreditCardBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    limit: int = Field(..., gt=0, description="Limit in cents, must be positive")
    closing_day: int = Field(..., ge=1, le=31)
    due_day: int = Field(..., ge=1, le=31)

class CreditCardCreate(CreditCardBase):
    pass

class CreditCardUpdate(CreditCardBase):
    pass

class CreditCardResponse(CreditCardBase):
    id: UUID
    user_id: UUID
    created_at: datetime

    class Config:
        from_attributes = True

class CreditCardSummaryResponse(BaseModel):
    id: UUID
    name: str
    limit: int
    closing_day: int
    due_day: int
    current_invoice_amount: int
    available_limit: int
