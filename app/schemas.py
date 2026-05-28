from pydantic import BaseModel, Field
from typing import List, Dict

class IngredientItem(BaseModel):
    name: str = Field(..., description="English name of the ingredient (e.g., 'Tofu', 'Soy Sauce')")
    amount: str = Field(..., description="Amount/quantity of the ingredient (e.g., '1 block', '2 tbsp', '150g')")

class SubstituteItem(BaseModel):
    original: str = Field(..., description="Name of the traditional Korean ingredient that is hard to find locally (e.g., 'Gochujang')")
    substitute: str = Field(..., description="Easy-to-find local substitute ingredient (e.g., 'Sriracha mixed with maple syrup')")
    reason: str = Field(..., description="Reason for substitution or advice on where to buy it")

class RecipeTranslationResponse(BaseModel):
    english_name: str = Field(..., description="Appealing and descriptive translated English name of the menu (e.g., 'Spicy Braised Tofu (Dubu-Jorim)')")
    english_ingredients: List[IngredientItem] = Field(..., description="List of recipe ingredients translated into English with adjusted amounts")
    local_substitutes: List[SubstituteItem] = Field(..., description="List of local substitutes for traditional ingredients that might be hard to find in Western grocery stores")
    instructions: List[str] = Field(..., description="Step-by-step cooking instructions written in clear English")
    seo_description: str = Field(..., description="Short, engaging SEO-friendly description for Google, Pinterest, and Instagram metadata (max 160 characters)")
    nutrition_info: Dict[str, str] = Field(..., description="Rough nutritional breakdown including calories, carbohydrates, protein, and fat (e.g., {'calories': '350 kcal', 'carbs': '40g', 'protein': '15g', 'fat': '10g'})")
