import os

DATABASE_URL = os.environ.get("DATABASE_URL", "/app/data/seasons.db")
ENV = os.environ.get("ENV", "development")

AISLE_CATEGORIES = [
    ("produce", "Produce"),
    ("dairy", "Dairy & Eggs"),
    ("meat", "Meat & Seafood"),
    ("bakery", "Bakery & Bread"),
    ("frozen", "Frozen"),
    ("canned", "Canned & Jarred"),
    ("grains", "Grains, Pasta & Rice"),
    ("spices", "Spices & Seasonings"),
    ("oils", "Oils & Vinegars"),
    ("snacks", "Snacks & Nuts"),
    ("beverages", "Beverages"),
    ("baking", "Baking"),
    ("condiments", "Condiments & Sauces"),
    ("other", "Other"),
]
