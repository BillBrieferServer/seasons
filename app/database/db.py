import sqlite3
import os

_db_path = None


def init_db(path: str):
    global _db_path
    _db_path = path
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = get_db()
    conn.executescript("""
        PRAGMA journal_mode=WAL;
        PRAGMA foreign_keys=ON;

        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        );

        INSERT OR IGNORE INTO settings (key, value) VALUES
            ('business_name', 'Seasons Care Services'),
            ('tagline', 'Personal Support for Every Season'),
            ('legal_name', 'Vita Nova LLC'),
            ('phone', '208-604-2139'),
            ('email', 'julie@seasonscareservices.com'),
            ('address', 'Pocatello, Idaho'),
            ('service_area', 'Pocatello, Chubbuck, Bannock County');

        CREATE TABLE IF NOT EXISTS recipes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT,
            base_servings INTEGER NOT NULL DEFAULT 4,
            prep_time_minutes INTEGER,
            cook_time_minutes INTEGER,
            source TEXT,
            source_url TEXT,
            status TEXT NOT NULL DEFAULT 'review',
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS dietary_tags (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL,
            color TEXT DEFAULT '#808D86'
        );

        INSERT OR IGNORE INTO dietary_tags (name, display_name, color) VALUES
            ('heart-healthy',      'Heart-Healthy',      '#993A31'),
            ('diabetic-friendly',  'Diabetic-Friendly',  '#6B8E5B'),
            ('anti-inflammatory',  'Anti-Inflammatory',  '#C17D3B'),
            ('bone-health',        'Bone Health',        '#5B7E8E'),
            ('digestive-wellness', 'Digestive Wellness', '#7B6B8E'),
            ('soft-foods',         'Soft Foods',         '#8E7B5B'),
            ('high-protein',       'High-Protein',       '#5B6B8E'),
            ('calorie-dense',      'Calorie-Dense',      '#8E5B6B'),
            ('low-sugar',          'Low-Sugar',          '#5B8E7B'),
            ('low-sodium',         'Low-Sodium',         '#6B8E8E'),
            ('general-healthy',    'General Healthy',    '#808D86'),
            ('freezer-friendly',   'Freezer-Friendly',   '#4A7FA5');

        CREATE TABLE IF NOT EXISTS recipe_tags (
            recipe_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (recipe_id, tag_id),
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES dietary_tags(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS recipe_ingredients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            amount REAL,
            unit TEXT,
            aisle_category TEXT DEFAULT 'other',
            sort_order INTEGER DEFAULT 0,
            notes TEXT,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS recipe_steps (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            recipe_id INTEGER NOT NULL,
            step_number INTEGER NOT NULL,
            instruction TEXT NOT NULL,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            first_name TEXT NOT NULL,
            last_name TEXT NOT NULL,
            dietary_notes TEXT,
            status TEXT DEFAULT 'active',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS client_tags (
            client_id INTEGER NOT NULL,
            tag_id INTEGER NOT NULL,
            PRIMARY KEY (client_id, tag_id),
            FOREIGN KEY (client_id) REFERENCES clients(id) ON DELETE CASCADE,
            FOREIGN KEY (tag_id) REFERENCES dietary_tags(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS meal_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            week_start DATE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS meal_plan_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meal_plan_id INTEGER NOT NULL,
            recipe_id INTEGER NOT NULL,
            day_of_week INTEGER,
            meal_type TEXT DEFAULT 'dinner',
            servings INTEGER NOT NULL DEFAULT 2,
            FOREIGN KEY (meal_plan_id) REFERENCES meal_plans(id) ON DELETE CASCADE,
            FOREIGN KEY (recipe_id) REFERENCES recipes(id)
        );

        CREATE TABLE IF NOT EXISTS shopping_lists (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            meal_plan_id INTEGER,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (meal_plan_id) REFERENCES meal_plans(id)
        );

        CREATE TABLE IF NOT EXISTS shopping_list_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            shopping_list_id INTEGER NOT NULL,
            ingredient_name TEXT NOT NULL,
            amount REAL,
            unit TEXT,
            aisle_category TEXT DEFAULT 'other',
            checked INTEGER DEFAULT 0,
            FOREIGN KEY (shopping_list_id) REFERENCES shopping_lists(id) ON DELETE CASCADE
        );

        CREATE TABLE IF NOT EXISTS meal_deliveries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER NOT NULL,
            recipe_id INTEGER NOT NULL,
            delivery_date DATE NOT NULL,
            servings INTEGER DEFAULT 1,
            notes TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (client_id) REFERENCES clients(id),
            FOREIGN KEY (recipe_id) REFERENCES recipes(id)
        );
    """)
    conn.close()
    print(f"Database initialized at {path}")


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn
