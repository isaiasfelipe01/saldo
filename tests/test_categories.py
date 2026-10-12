"""Category rename/move/delete planning: the "Categoria / Subcategoria" names stay consistent."""

import pytest
from fastapi import HTTPException

from app.current import plan_category_delete, plan_category_rename

CATEGORIES = [
    {"id": "food", "name": "Alimentação", "type": "expense"},
    {"id": "rest", "name": "Alimentação / Restaurante", "type": "expense"},
    {"id": "market", "name": "Alimentação / Mercado", "type": "expense"},
    {"id": "home", "name": "Moradia", "type": "expense"},
    {"id": "rent", "name": "Moradia / Aluguel", "type": "expense"},
    {"id": "salary", "name": "Salário", "type": "income"},
    {"id": "food-income", "name": "Alimentação / Vale", "type": "income"},
]


def test_renaming_main_category_carries_subcategories():
    assert sorted(plan_category_rename(CATEGORIES, "food", "Comida")) == [
        ("food", "Comida"), ("market", "Comida / Mercado"), ("rest", "Comida / Restaurante")]


def test_moving_subcategory_to_another_main():
    assert plan_category_rename(CATEGORIES, "rest", "Moradia/  Restaurante") == [("rest", "Moradia / Restaurante")]


def test_subcategory_can_become_main_category():
    assert plan_category_rename(CATEGORIES, "market", "Mercado") == [("market", "Mercado")]


@pytest.mark.parametrize("category_id,name,status", [
    ("rest", "Moradia / Aluguel", 409),     # clashes with an existing name
    ("food", "moradia", 409),               # clash is case-insensitive
    ("rest", "Viagem / Restaurante", 422),  # unknown main category
    ("food", "Moradia / Alimentação", 422), # a main with subcategories cannot be nested
    ("rest", "A / B / C", 422),             # one level only
    ("rest", " / ", 422),
    ("missing", "Nova", 404),
])
def test_invalid_renames(category_id, name, status):
    with pytest.raises(HTTPException) as error:
        plan_category_rename(CATEGORIES, category_id, name)
    assert error.value.status_code == status


def test_unchanged_name_produces_no_updates():
    assert plan_category_rename(CATEGORIES, "home", "Moradia") == []


def test_delete_main_takes_its_subcategories_of_the_same_type_only():
    assert sorted(plan_category_delete(CATEGORIES, "food")) == ["food", "market", "rest"]
    assert plan_category_delete(CATEGORIES, "rent") == ["rent"]
