from __future__ import annotations

from services.meals import MealPlanService


def test_curated_meal_plans_are_complete_low_carb_and_varied() -> None:
    for days in (7, 10):
        plan = MealPlanService._fallback_plan(days, people=2)

        MealPlanService._validate(plan, days)

        meal_names = {
            day[meal]["name"]
            for day in plan["days"]
            for meal in ("breakfast", "lunch", "dinner")
        }
        assert len(meal_names) == days * 3
        assert plan["generation_method"] == "curated_fallback"
        assert all(item["amount"] for item in plan["shopping_list"])
