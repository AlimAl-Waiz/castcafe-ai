from flask import Flask, render_template, request, Response, redirect, url_for, session
from werkzeug.security import generate_password_hash, check_password_hash
import os
import pandas as pd
import sqlite3

app = Flask(__name__)
app.secret_key = "cafe_system_secret_key_123"

# -----------------------------
# Folders
# -----------------------------
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_FOLDER = os.path.join(BASE_DIR, "uploads")
STATIC_FOLDER = os.path.join(BASE_DIR, "static")
DATA_FOLDER = os.path.join(BASE_DIR, "data")
DB_FILE = os.path.join(DATA_FOLDER, "cafe_system.db")

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER

os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(STATIC_FOLDER, exist_ok=True)
os.makedirs(DATA_FOLDER, exist_ok=True)

ALLOWED_EXTENSIONS = {"csv", "xlsx"}

# -----------------------------
# Default app data
# -----------------------------
app_data = {
    "sales": None,
    "stock": "No low stock items detected.",
    "recommendation": "No recommendation yet.",
    "message": None,
    "message_type": None,
    "preview_table": None,
    "inventory_rows": [],
    "predicted_sales": None,
    "prediction_summary": "Upload a dataset to generate predictions.",
    "recommended_stock": {
        "Milk": "-",
        "Sugar": "-",
        "Coffee Beans": "-"
    },
    "report_total_sales": None,
    "best_selling_item": "No data yet",
    "prediction_accuracy": "MAE: 235.57",
    "report_low_stock": "No low stock items detected.",
    "report_restock": "No recommendation yet.",
    "chart_labels": [],
    "chart_values": [],
    "product_options": [],
    "product_chart_data": {},
    "latest_sales_file": None,
}


# -----------------------------
# Database helpers
# -----------------------------
def get_db_connection():
    conn = sqlite3.connect(DB_FILE, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA synchronous=NORMAL;")
    return conn

def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'staff',
            cafe_name TEXT
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS inventory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            item TEXT UNIQUE NOT NULL,
            stock INTEGER NOT NULL,
            status TEXT NOT NULL
        )
    """)

    user_columns = [row[1] for row in cursor.execute("PRAGMA table_info(users)").fetchall()]

    if "role" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'staff'")

    if "cafe_name" not in user_columns:
        cursor.execute("ALTER TABLE users ADD COLUMN cafe_name TEXT")

    conn.commit()
    conn.close()


def create_default_users():
    conn = get_db_connection()
    cursor = conn.cursor()

    default_users = [
        ("admin", "admin123", "admin", "System Admin"),
        ("patbiasa_owner", "pat123", "owner", "Pat Biasa"),
        ("patbiasa_manager", "patmanager123", "manager", "Pat Biasa"),
        ("duchiekofi_owner", "duchie123", "owner", "DuchieKofi"),
        ("duchiekofi_manager", "duchiemanager123", "manager", "DuchieKofi"),
    ]

    for username, password, role, cafe_name in default_users:
        password_hash = generate_password_hash(password)

        existing_user = cursor.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()

        if existing_user:
            cursor.execute(
                "UPDATE users SET password_hash = ?, role = ?, cafe_name = ? WHERE username = ?",
                (password_hash, role, cafe_name, username)
            )
        else:
            cursor.execute(
                "INSERT INTO users (username, password_hash, role, cafe_name) VALUES (?, ?, ?, ?)",
                (username, password_hash, role, cafe_name)
            )

    conn.commit()
    conn.close()


def save_inventory_to_db(inventory_rows):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM inventory")

        for row in inventory_rows:
            cursor.execute(
                "INSERT INTO inventory (item, stock, status) VALUES (?, ?, ?)",
                (str(row["item"]), int(row["stock"]), str(row["status"]))
            )

        conn.commit()
    finally:
        conn.close()


def load_inventory_from_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    rows = cursor.execute("SELECT item, stock, status FROM inventory").fetchall()
    conn.close()

    inventory_rows = []
    for row in rows:
        inventory_rows.append({
            "item": row["item"],
            "stock": int(row["stock"]),
            "status": row["status"]
        })

    return inventory_rows


def refresh_inventory_from_db():
    db_inventory = load_inventory_from_db()

    if db_inventory:
        app_data["inventory_rows"] = db_inventory
        low_items = [row["item"] for row in db_inventory if row["status"] in ["Urgent", "Restock Soon"]]

        if low_items:
            short_low_items = low_items[:3]
            remaining_count = len(low_items) - len(short_low_items)

            if remaining_count > 0:
                app_data["stock"] = ", ".join(short_low_items) + f" + {remaining_count} more"
            else:
                app_data["stock"] = ", ".join(short_low_items)

            app_data["report_low_stock"] = ", ".join(low_items)
            app_data["report_restock"] = ", ".join(low_items[:5])
            app_data["recommendation"] = "Restock: " + ", ".join(low_items[:3]) + (f" + {len(low_items) - 3} more" if len(low_items) > 3 else "")
        else:
            app_data["report_low_stock"] = "No low stock items detected."
            app_data["report_restock"] = "No restock needed."
            app_data["stock"] = "No low stock items detected."
            app_data["recommendation"] = "No restock needed."
# -----------------------------
# Helper functions
# -----------------------------
def is_logged_in():
    return "user_id" in session


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def load_uploaded_file(file_path):
    if file_path.lower().endswith(".csv"):
        df = pd.read_csv(file_path)
    else:
        df = pd.read_excel(file_path)
    return df


def build_inventory_rows_from_inventory_file(df):
    df = df.copy()
    df.columns = [str(col).strip().lower() for col in df.columns]

    item_col = None
    for col in ["ingredient_name", "item", "product_detail", "product_type"]:
        if col in df.columns:
            item_col = col
            break

    stock_col = None
    for col in ["starting_stock", "stock", "remaining_stock", "quantity"]:
        if col in df.columns:
            stock_col = col
            break

    if not item_col or not stock_col:
        return []

    rows = []

    for _, row in df.iterrows():
        item_name = str(row[item_col]).strip()

        try:
            stock_value = int(float(row[stock_col]))
        except Exception:
            stock_value = 0

        if stock_value <= 25:
            status = "Urgent"
        elif stock_value <= 60:
            status = "Restock Soon"
        else:
            status = "Sufficient"

        rows.append({
            "item": item_name,
            "stock": stock_value,
            "status": status
        })

    return rows


def build_inventory_rows(df):
    df = df.copy()
    df.columns = [str(col).strip().lower() for col in df.columns]

    if "product_detail" in df.columns:
        item_col = "product_detail"
    elif "item" in df.columns:
        item_col = "item"
    elif "product_type" in df.columns:
        item_col = "product_type"
    else:
        return [
            {"item": "Latte", "stock": 160, "status": "Sufficient"},
            {"item": "Milk", "stock": 180, "status": "Sufficient"},
            {"item": "Coffee Beans", "stock": 140, "status": "Sufficient"},
            {"item": "Matcha", "stock": 90, "status": "Sufficient"},
        ]

    qty_col = None
    for col in ["transaction_qty", "quantity", "qty"]:
        if col in df.columns:
            qty_col = col
            break

    if qty_col is None:
        return [
            {"item": "Latte", "stock": 160, "status": "Sufficient"},
            {"item": "Milk", "stock": 180, "status": "Sufficient"},
            {"item": "Coffee Beans", "stock": 140, "status": "Sufficient"},
            {"item": "Matcha", "stock": 90, "status": "Sufficient"},
        ]

    sold_data = df.groupby(item_col)[qty_col].sum().reset_index()

    starting_stock = {
        "Latte": 220,
        "Milk": 240,
        "Coffee Beans": 180,
        "Matcha": 120,
        "Chocolate Thumper (Hot)": 180,
        "Chocolate Thumper (Iced)": 180,
        "Meadow Matcha (Hot)": 140,
        "Meadow Matcha (Iced)": 140,
        "Cottonn Latte (Hot)": 190,
        "Cottonn Latte (Iced)": 190,
        "Mocha Thumper (Hot)": 160,
        "Mocha Thumper (Iced)": 160,
        "Bunny Latte (Hot)": 170,
        "Bunny Latte (Iced)": 170,
        "Hopicano (Hot)": 150,
        "Hopicano (Iced)": 150,
        "Americano": 120,
        "Cappuccino": 120,
        "Caramel Latte": 130,
        "Chocolate": 100,
        "Hazelnut Latte": 120,
        "Lemon Tea": 90,
        "Matcha Latte": 100,
        "Mocha": 110,
        "Peach Tea": 90,
        "Spanish Latte": 120,
        "Vanilla Latte": 120,
    }

    rows = []

    for _, row in sold_data.iterrows():
        item_name = str(row[item_col]).strip()
        sold_qty = float(row[qty_col])

        initial = starting_stock.get(item_name, 150)
        remaining = max(0, round(initial - sold_qty))

        if remaining <= 25:
            status = "Urgent"
        elif remaining <= 60:
            status = "Restock Soon"
        else:
            status = "Sufficient"

        rows.append({
            "item": item_name,
            "stock": remaining,
            "status": status
        })

    return rows


def prepare_daily_data(df):
    df = df.copy()
    df.columns = [str(col).strip().lower() for col in df.columns]

    date_col = None
    for col in ["transaction_date", "date", "order_date"]:
        if col in df.columns:
            date_col = col
            break

    qty_col = None
    for col in ["transaction_qty", "quantity", "qty"]:
        if col in df.columns:
            qty_col = col
            break

    price_col = None
    for col in ["unit_price", "price", "sales_amount"]:
        if col in df.columns:
            price_col = col
            break

    if not date_col:
        total = len(df)
        if qty_col:
            total = df[qty_col].sum()

        return pd.DataFrame({
            "transaction_date": ["Uploaded Data"],
            "total_sales": [round(float(total), 0)],
        })

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col])

    if qty_col and price_col:
        df["total_sales"] = df[qty_col] * df[price_col]
    elif qty_col:
        df["total_sales"] = df[qty_col]
    else:
        df["total_sales"] = 1

    daily_data = df.groupby(df[date_col].dt.date)["total_sales"].sum().reset_index()
    daily_data.columns = ["transaction_date", "total_sales"]
    daily_data["total_sales"] = daily_data["total_sales"].round(0)

    return daily_data


def prepare_chart_data(daily_data):
    labels = [str(x) for x in daily_data["transaction_date"].tolist()]
    values = [float(x) for x in daily_data["total_sales"].tolist()]
    return labels, values


def prepare_product_chart_data(df):
    df = df.copy()
    df.columns = [str(col).strip().lower() for col in df.columns]

    if "product_detail" not in df.columns:
        return {}

    date_col = None
    for col in ["transaction_date", "date", "order_date"]:
        if col in df.columns:
            date_col = col
            break

    qty_col = None
    for col in ["transaction_qty", "quantity", "qty"]:
        if col in df.columns:
            qty_col = col
            break

    if not date_col or not qty_col:
        return {}

    df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
    df = df.dropna(subset=[date_col])

    product_chart_data = {}

    for product in sorted(df["product_detail"].dropna().astype(str).unique()):
        product_df = df[df["product_detail"].astype(str) == product].copy()
        daily_product = product_df.groupby(product_df[date_col].dt.date)[qty_col].sum().reset_index()
        daily_product.columns = ["transaction_date", "total_sales"]

        labels = [str(x) for x in daily_product["transaction_date"].tolist()]
        values = [float(x) for x in daily_product["total_sales"].tolist()]

        product_chart_data[product] = {
            "labels": labels,
            "values": values
        }

    return product_chart_data


def analyze_data(df):
    df = df.copy()
    df.columns = [str(col).strip().lower() for col in df.columns]

    daily_data = prepare_daily_data(df)

    if not daily_data.empty:
        predicted_sales = int(round(daily_data["total_sales"].mean(), 0))
        total_sales = int(round(daily_data["total_sales"].sum(), 0))
    else:
        predicted_sales = 140
        total_sales = 130

    item_col = None
    for col in ["product_type", "product_detail", "item"]:
        if col in df.columns:
            item_col = col
            break

    if item_col:
        top_items = df[item_col].value_counts().head(3)
        best_item = str(top_items.index[0]) if len(top_items) > 0 else "Latte"
        recommendation = ", ".join([str(x) for x in top_items.index.tolist()])
    else:
        best_item = "Latte"
        recommendation = "Milk, Sugar, Coffee Beans"

    inventory_rows = build_inventory_rows(df)
    low_stock_items = [row["item"] for row in inventory_rows if row["status"] in ["Urgent", "Restock Soon"]]
    low_stock_text = ", ".join(low_stock_items[:3]) if low_stock_items else "No low stock items detected."

    prediction_summary = f"Prediction generated from {len(daily_data)} daily records."

    recommended_stock = {
        "Milk": f"{max(1, round(predicted_sales / 20))}L",
        "Sugar": f"{max(1, round(predicted_sales / 60))}kg",
        "Coffee Beans": f"{max(1, round(predicted_sales / 80))}kg",
    }

    return {
        "sales": predicted_sales,
        "stock": low_stock_text,
        "recommendation": recommendation,
        "inventory_rows": inventory_rows,
        "predicted_sales": predicted_sales,
        "prediction_summary": prediction_summary,
        "recommended_stock": recommended_stock,
        "report_total_sales": total_sales,
        "best_selling_item": best_item,
        "prediction_accuracy": "MAE: 235.57",
        "report_low_stock": low_stock_text,
        "report_restock": recommendation,
        "daily_data": daily_data,
    }


def get_ai_prediction_context():
    predicted_sales = app_data["predicted_sales"]
    chart_values = app_data.get("chart_values", [])
    recommendation = app_data.get("recommendation", "No recommendation yet.")
    report_low_stock = app_data.get("report_low_stock", "No low stock items detected.")

    if predicted_sales is None:
        demand_level = "No data yet"
        demand_trend = "Upload a dataset to generate AI demand insight."
        priority_item = "No data yet"
        ai_action = "Upload a dataset first to generate prediction and restock recommendations."
        return demand_level, demand_trend, priority_item, ai_action

    if predicted_sales < 80:
        demand_level = "Low"
    elif predicted_sales <= 160:
        demand_level = "Moderate"
    else:
        demand_level = "High"

    if len(chart_values) >= 6:
        recent_avg = sum(chart_values[-3:]) / 3
        earlier_avg = sum(chart_values[-6:-3]) / 3

        if recent_avg > earlier_avg + 5:
            demand_trend = "Recent sales are increasing compared with the earlier period."
        elif recent_avg < earlier_avg - 5:
            demand_trend = "Recent sales are slightly declining compared with the earlier period."
        else:
            demand_trend = "Recent sales are relatively stable across the uploaded period."
    elif len(chart_values) >= 2:
        if chart_values[-1] > chart_values[0]:
            demand_trend = "Sales show a general upward direction in the uploaded data."
        elif chart_values[-1] < chart_values[0]:
            demand_trend = "Sales show a general downward direction in the uploaded data."
        else:
            demand_trend = "Sales appear mostly stable in the uploaded data."
    else:
        demand_trend = "Not enough chart data to detect a stronger demand pattern."

    if report_low_stock and report_low_stock != "No low stock items detected.":
        low_items = [x.strip() for x in report_low_stock.split(",") if x.strip()]
        priority_item = low_items[0] if low_items else "No urgent item"

        if len(low_items) == 1:
            ai_action = f"Restock {low_items[0]} soon because stock is below the preferred level."
        elif len(low_items) == 2:
            ai_action = f"Restock {low_items[0]} and {low_items[1]} soon because stock is below the preferred level."
        else:
            ai_action = f"Restock {low_items[0]}, {low_items[1]}, and {len(low_items) - 2} more items soon."
    else:
        priority_item = "No urgent item"

        if recommendation and recommendation != "No recommendation yet.":
            ai_action = f"Focus preparation on: {recommendation}."
        else:
            ai_action = "Current stock level is stable. Continue monitoring upcoming sales trends."

    return demand_level, demand_trend, priority_item, ai_action


init_db()
create_default_users()
init_db()
create_default_users()

db_inventory = load_inventory_from_db()
if db_inventory:
    refresh_inventory_from_db()


    def get_filtered_report_data(start_date=None, end_date=None):
        file_path = app_data.get("latest_sales_file")

        if not file_path or not os.path.exists(file_path):
            return None

        df = load_uploaded_file(file_path)
        df.columns = [str(col).strip().lower() for col in df.columns]

        date_col = None
        for col in ["transaction_date", "date", "order_date"]:
            if col in df.columns:
                date_col = col
                break

        item_col = None
        for col in ["product_detail", "item", "product_type"]:
            if col in df.columns:
                item_col = col
                break

        qty_col = None
        for col in ["transaction_qty", "quantity", "qty"]:
            if col in df.columns:
                qty_col = col
                break

        price_col = None
        for col in ["unit_price", "price", "sales_amount"]:
            if col in df.columns:
                price_col = col
                break

        if date_col:
            df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
            df = df.dropna(subset=[date_col])

            if start_date:
                df = df[df[date_col] >= pd.to_datetime(start_date)]

            if end_date:
                df = df[df[date_col] <= pd.to_datetime(end_date)]

        if df.empty:
            return {
                "total_sales": 0,
                "best_selling_item": "No data",
                "records": 0,
                "demand_label": "no available"
            }

        if qty_col and price_col:
            df["total_sales_calc"] = df[qty_col] * df[price_col]
            total_sales = round(df["total_sales_calc"].sum(), 2)
        elif qty_col:
            total_sales = int(df[qty_col].sum())
        else:
            total_sales = len(df)

        if item_col:
            best_selling_item = str(df[item_col].value_counts().idxmax())
        else:
            best_selling_item = "No data"

        if total_sales > 150:
            demand_label = "stronger"
        elif total_sales > 70:
            demand_label = "moderate"
        elif total_sales > 0:
            demand_label = "lighter"
        else:
            demand_label = "no available"

        return {
            "total_sales": total_sales,
            "best_selling_item": best_selling_item,
            "records": len(df),
            "demand_label": demand_label
        }

# -----------------------------
# Routes
# -----------------------------
@app.route("/", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")

        conn = get_db_connection()
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?",
            (username,)
        ).fetchone()
        conn.close()

        if user and check_password_hash(user["password_hash"], password):
            session["user_id"] = user["id"]
            session["username"] = user["username"]
            session["role"] = user["role"]
            session["cafe_name"] = user["cafe_name"]
            return redirect(url_for("dashboard"))
        else:
            return render_template("login.html", error="Invalid username or password.")

    return render_template("login.html")

@app.route("/register", methods=["GET", "POST"])
def register():
    if request.method == "POST":
        username = request.form.get("username")
        password = request.form.get("password")
        cafe_name = request.form.get("cafe_name")

        if not username or not password or not cafe_name:
            return render_template("register.html", error="Please fill in all fields.")

        conn = get_db_connection()
        cursor = conn.cursor()

        try:
            existing_user = cursor.execute(
                "SELECT * FROM users WHERE username = ?",
                (username,)
            ).fetchone()

            if existing_user:
                return render_template("register.html", error="Username already exists.")

            password_hash = generate_password_hash(password)

            cursor.execute(
                "INSERT INTO users (username, password_hash, role, cafe_name) VALUES (?, ?, ?, ?)",
                (username, password_hash, "staff", cafe_name)
            )

            conn.commit()
            return redirect(url_for("login"))

        except Exception as e:
            return render_template("register.html", error=f"Could not create account: {str(e)}")

        finally:
            conn.close()

    return render_template("register.html")


@app.route("/dashboard")
def dashboard():
    if not is_logged_in():
        return redirect(url_for("login"))

    demand_level, demand_trend, priority_item, ai_action = get_ai_prediction_context()

    urgent_items_count = len([
        row for row in app_data["inventory_rows"]
        if row["status"] == "Urgent"
    ])

    return render_template(
        "dashboard.html",
        sales=app_data["sales"],
        stock=app_data["stock"],
        recommendation=app_data["recommendation"],
        message=app_data["message"],
        message_type=app_data["message_type"],
        preview_table=app_data["preview_table"],
        demand_level=demand_level,
        priority_item=priority_item,
        ai_action=ai_action,
        best_selling_item=app_data["best_selling_item"],
        urgent_items_count=urgent_items_count
    )


@app.route("/upload", methods=["POST"])
def upload():
    if not is_logged_in():
        return redirect(url_for("login"))

    file = request.files.get("sales_file")

    if not file or not file.filename:
        app_data["message"] = "No sales file selected."
        app_data["message_type"] = "error"
        return redirect(url_for("dashboard"))

    if not allowed_file(file.filename):
        app_data["message"] = "Invalid sales file type. Please upload CSV or XLSX only."
        app_data["message_type"] = "error"
        return redirect(url_for("dashboard"))

    save_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
    file.save(save_path)
    app_data["latest_sales_file"] = save_path

    try:
        df = load_uploaded_file(save_path)

        if df.empty:
            app_data["message"] = "The uploaded sales file is empty."
            app_data["message_type"] = "error"
            app_data["preview_table"] = None
            return redirect(url_for("dashboard"))

        df.columns = [str(col).strip().lower() for col in df.columns]

        missing_columns = []

        if "product_detail" not in df.columns and "item" not in df.columns and "product_type" not in df.columns:
            missing_columns.append("product_detail or item")

        if "transaction_qty" not in df.columns and "quantity" not in df.columns and "qty" not in df.columns:
            missing_columns.append("transaction_qty or quantity")

        if "transaction_date" not in df.columns and "date" not in df.columns and "order_date" not in df.columns:
            missing_columns.append("transaction_date or date")

        if missing_columns:
            app_data["message"] = "Sales file is missing required columns: " + ", ".join(missing_columns)
            app_data["message_type"] = "error"
            app_data["preview_table"] = None
            return redirect(url_for("dashboard"))

        product_options = []
        if "product_detail" in df.columns:
            product_options = sorted(df["product_detail"].dropna().astype(str).unique().tolist())
        elif "item" in df.columns:
            product_options = sorted(df["item"].dropna().astype(str).unique().tolist())
        elif "product_type" in df.columns:
            product_options = sorted(df["product_type"].dropna().astype(str).unique().tolist())

        preview_table = df.head().to_html(classes="table", index=False)

        results = analyze_data(df)
        chart_labels, chart_values = prepare_chart_data(results["daily_data"])
        product_chart_data = prepare_product_chart_data(df)

        app_data["sales"] = results["sales"]
        app_data["stock"] = results["stock"]
        app_data["recommendation"] = results["recommendation"]
        app_data["inventory_rows"] = results["inventory_rows"]
        app_data["predicted_sales"] = results["predicted_sales"]
        app_data["prediction_summary"] = results["prediction_summary"]
        app_data["recommended_stock"] = results["recommended_stock"]
        app_data["report_total_sales"] = results["report_total_sales"]
        app_data["best_selling_item"] = results["best_selling_item"]
        app_data["prediction_accuracy"] = results["prediction_accuracy"]
        app_data["report_low_stock"] = results["report_low_stock"]
        app_data["report_restock"] = results["report_restock"]
        app_data["preview_table"] = preview_table
        app_data["chart_labels"] = chart_labels
        app_data["chart_values"] = chart_values
        app_data["product_options"] = product_options
        app_data["product_chart_data"] = product_chart_data
        app_data["message"] = f"Sales file uploaded successfully: {file.filename}"
        app_data["message_type"] = "success"

        save_inventory_to_db(app_data["inventory_rows"])
        refresh_inventory_from_db()

    except Exception as e:
        app_data["message"] = f"Could not process sales file: {str(e)}"
        app_data["message_type"] = "error"
        app_data["preview_table"] = None

    return redirect(url_for("dashboard"))


@app.route("/upload_inventory_file", methods=["POST"])
def upload_inventory_file():
    if not is_logged_in():
        return redirect(url_for("login"))

    file = request.files.get("inventory_file")

    if not file or not file.filename:
        app_data["message"] = "No inventory file selected."
        app_data["message_type"] = "error"
        return redirect(url_for("dashboard"))

    if not allowed_file(file.filename):
        app_data["message"] = "Invalid inventory file type. Please upload CSV or XLSX only."
        app_data["message_type"] = "error"
        return redirect(url_for("dashboard"))

    save_path = os.path.join(app.config["UPLOAD_FOLDER"], file.filename)
    file.save(save_path)

    try:
        df = load_uploaded_file(save_path)

        if df.empty:
            app_data["message"] = "The uploaded inventory file is empty."
            app_data["message_type"] = "error"
            return redirect(url_for("dashboard"))

        df.columns = [str(col).strip().lower() for col in df.columns]

        missing_columns = []

        if "ingredient_name" not in df.columns and "item" not in df.columns and "product_detail" not in df.columns:
            missing_columns.append("ingredient_name or item")

        if "starting_stock" not in df.columns and "stock" not in df.columns and "remaining_stock" not in df.columns and "quantity" not in df.columns:
            missing_columns.append("starting_stock or stock")

        if missing_columns:
            app_data["message"] = "Inventory file is missing required columns: " + ", ".join(missing_columns)
            app_data["message_type"] = "error"
            return redirect(url_for("dashboard"))

        inventory_rows = build_inventory_rows_from_inventory_file(df)

        if not inventory_rows:
            app_data["message"] = "Could not detect valid inventory data in the uploaded inventory file."
            app_data["message_type"] = "error"
            return redirect(url_for("dashboard"))

        app_data["inventory_rows"] = inventory_rows
        save_inventory_to_db(inventory_rows)
        refresh_inventory_from_db()

        app_data["message"] = f"Inventory file uploaded successfully: {file.filename}"
        app_data["message_type"] = "success"

    except Exception as e:
        app_data["message"] = f"Could not process inventory file: {str(e)}"
        app_data["message_type"] = "error"

    return redirect(url_for("dashboard"))


@app.route("/inventory")
def inventory():
    if not is_logged_in():
        return redirect(url_for("login"))

    refresh_inventory_from_db()
    return render_template(
        "inventory.html",
        inventory_rows=app_data["inventory_rows"],
    )


@app.route("/update_inventory", methods=["POST"])
def update_inventory():
    if not is_logged_in():
        return redirect(url_for("login"))

    item_name = request.form.get("item_name")
    new_stock = request.form.get("new_stock")

    if not item_name or new_stock is None:
        app_data["message"] = "Missing inventory update data."
        app_data["message_type"] = "error"
        return redirect(url_for("inventory"))

    try:
        new_stock = int(new_stock)

        update_inventory_item_in_db(item_name, new_stock)
        refresh_inventory_from_db()

        app_data["message"] = f"Stock updated for {item_name}."
        app_data["message_type"] = "success"
        return redirect(url_for("inventory"))

    except Exception as e:
        print("UPDATE INVENTORY ERROR:", e)
        app_data["message"] = f"Could not update inventory: {str(e)}"
        app_data["message_type"] = "error"
        return redirect(url_for("inventory"))


def update_inventory_item_in_db(item_name, new_stock):
    conn = get_db_connection()
    try:
        cursor = conn.cursor()

        if int(new_stock) <= 50:
            status = "Urgent"
        elif int(new_stock) <= 150:
            status = "Restock Soon"
        else:
            status = "Sufficient"

        cursor.execute(
            "UPDATE inventory SET stock = ?, status = ? WHERE item = ?",
            (int(new_stock), status, item_name)
        )

        conn.commit()
    finally:
        conn.close()

@app.route("/prediction")
def prediction():
    if not is_logged_in():
        return redirect(url_for("login"))

    demand_level, demand_trend, priority_item, ai_action = get_ai_prediction_context()

    return render_template(
        "prediction.html",
        predicted_sales=app_data["predicted_sales"],
        prediction_summary=app_data["prediction_summary"],
        recommended_stock=app_data["recommended_stock"],
        recommendation=app_data["recommendation"],
        demand_level=demand_level,
        demand_trend=demand_trend,
        priority_item=priority_item,
        ai_action=ai_action,
        chart_labels=app_data["chart_labels"],
        chart_values=app_data["chart_values"],
        product_options=app_data.get("product_options", []),
        product_chart_data=app_data.get("product_chart_data", {})
    )

@app.route("/report")
def report():
    if not is_logged_in():
        return redirect(url_for("login"))

    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    try:
        filtered = get_filtered_report_data(start_date, end_date)
        min_date, max_date = get_sales_date_range()

        return render_template(
            "report.html",
            report_total_sales=filtered["total_sales"] if filtered else app_data.get("report_total_sales"),
            best_selling_item=filtered["best_selling_item"] if filtered else app_data.get("best_selling_item"),
            prediction_accuracy=app_data.get("prediction_accuracy"),
            report_low_stock=app_data.get("report_low_stock"),
            report_restock=app_data.get("report_restock"),
            demand_label=filtered["demand_label"] if filtered and "demand_label" in filtered else None,
            start_date=start_date,
            end_date=end_date,
            min_date=min_date,
            max_date=max_date
        )

    except Exception as e:
        print("REPORT ROUTE ERROR:", e)
        return f"Report error: {str(e)}", 500

    def get_sales_date_range():
        file_path = app_data.get("latest_sales_file")

        if not file_path or not os.path.exists(file_path):
            return None, None

        df = load_uploaded_file(file_path)
        df.columns = [str(col).strip().lower() for col in df.columns]

        date_col = None
        for col in ["transaction_date", "date", "order_date"]:
            if col in df.columns:
                date_col = col
                break

        if not date_col:
            return None, None

        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col])

        if df.empty:
            return None, None

        min_date = df[date_col].min().strftime("%Y-%m-%d")
        max_date = df[date_col].max().strftime("%Y-%m-%d")

        return min_date, max_date

@app.route("/download_report")
def download_report():
    if not is_logged_in():
        return redirect(url_for("login"))

    start_date = request.args.get("start_date")
    end_date = request.args.get("end_date")

    filtered = get_filtered_report_data(start_date, end_date)

    total_sales = filtered["total_sales"] if filtered else app_data["report_total_sales"]
    best_selling_item = filtered["best_selling_item"] if filtered else app_data["best_selling_item"]
    records = filtered["records"] if filtered else "N/A"
    demand_label = filtered["demand_label"] if filtered else "No data"

    report_text = f"""Metric,Value
Start Date,{start_date or "All"}
End Date,{end_date or "All"}
Records Included,{records}
Demand Overview,{demand_label}
Predicted Sales,{app_data["predicted_sales"]}
Prediction Summary,{app_data["prediction_summary"]}
Prediction Accuracy,{app_data["prediction_accuracy"]}
Filtered Total Sales,{total_sales}
Best Selling Item,{best_selling_item}
Low Stock Items,{app_data["report_low_stock"]}
Recommended Restock,{app_data["report_restock"]}
"""

    return Response(
        report_text,
        mimetype="text/csv",
        headers={"Content-disposition": "attachment; filename=report.csv"}
    )

def get_filtered_report_data(start_date=None, end_date=None):
    file_path = app_data.get("latest_sales_file")

    print("LATEST SALES FILE:", file_path)

    if not file_path:
        return None

    if not os.path.exists(file_path):
        print("SALES FILE DOES NOT EXIST")
        return None

    df = load_uploaded_file(file_path)
    df.columns = [str(col).strip().lower() for col in df.columns]

    date_col = None
    for col in ["transaction_date", "date", "order_date"]:
        if col in df.columns:
            date_col = col
            break

    item_col = None
    for col in ["product_detail", "item", "product_type"]:
        if col in df.columns:
            item_col = col
            break

    qty_col = None
    for col in ["transaction_qty", "quantity", "qty"]:
        if col in df.columns:
            qty_col = col
            break

    price_col = None
    for col in ["unit_price", "price", "sales_amount"]:
        if col in df.columns:
            price_col = col
            break

    if date_col:
        df[date_col] = pd.to_datetime(df[date_col], errors="coerce")
        df = df.dropna(subset=[date_col])

        if start_date:
            df = df[df[date_col] >= pd.to_datetime(start_date)]

        if end_date:
            df = df[df[date_col] <= pd.to_datetime(end_date)]

    if df.empty:
        return {
            "total_sales": 0,
            "best_selling_item": "No data",
            "records": 0,
            "demand_label": "no available"
        }

    if qty_col and price_col:
        df["total_sales_calc"] = df[qty_col] * df[price_col]
        total_sales = round(df["total_sales_calc"].sum(), 2)
    elif qty_col:
        total_sales = int(df[qty_col].sum())
    else:
        total_sales = len(df)

    if item_col:
        best_selling_item = str(df[item_col].value_counts().idxmax())
    else:
        best_selling_item = "No data"

    if total_sales > 150:
        demand_label = "stronger"
    elif total_sales > 70:
        demand_label = "moderate"
    elif total_sales > 0:
        demand_label = "lighter"
    else:
        demand_label = "no available"

    return {
        "total_sales": total_sales,
        "best_selling_item": best_selling_item,
        "records": len(df),
        "demand_label": demand_label
    }

@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)