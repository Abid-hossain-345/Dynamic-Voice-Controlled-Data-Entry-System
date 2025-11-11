from flask import Flask, render_template, request, jsonify, send_file
import mysql.connector
import csv
import io
import os
import PyPDF2
import re
import spacy

app = Flask(__name__)

# ------------------------------------------------------------
# ✅ MySQL Config
# ------------------------------------------------------------
db_config = {
    "host": "localhost",
    "user": "root",
    "password": "root",
    "database": "voice_form",
    "port": 3306
}

UPLOAD_FOLDER = "uploads"
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# ------------------------------------------------------------
# ✅ Initialize DB
# ------------------------------------------------------------
def init_db():
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()

    # ✅ Create table if missing
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS submissions1 (
            id INT AUTO_INCREMENT PRIMARY KEY,
            name VARCHAR(255),
            age VARCHAR(50),
            country VARCHAR(255)
        )
    """)

    # ✅ Check if 'id' column exists
    cursor.execute("SHOW COLUMNS FROM submissions1 LIKE 'id'")
    id_column = cursor.fetchone()

    # ✅ Auto-fix: If 'id' column is missing, recreate table properly
    if not id_column:
        print("⚠️ FIXING TABLE: Missing 'id' column. Rebuilding safely...")

        # Step 1: Rename old table
        cursor.execute("RENAME TABLE submissions1 TO submissions1_old")

        # Step 2: Create new correct table
        cursor.execute("""
            CREATE TABLE submissions1 (
                id INT AUTO_INCREMENT PRIMARY KEY,
                name VARCHAR(255),
                age VARCHAR(50),
                country VARCHAR(255)
            )
        """)

        # Step 3: Copy matching columns
        cursor.execute("""
            INSERT INTO submissions1 (name, age, country)
            SELECT name, age, country FROM submissions1_old
        """)

        # Step 4: Drop old table
        cursor.execute("DROP TABLE submissions1_old")

        print("✅ FIX COMPLETE: Table repaired successfully.")

    conn.commit()
    conn.close()

init_db()

# ------------------------------------------------------------
# ✅ spaCy model
# ------------------------------------------------------------
nlp = spacy.load("en_core_web_sm")

FORM_FIELDS = {
    "name": {"ner": ["PERSON"], "keywords": ["name"], "regex": r"(?:my name is|name[:\s]+)([A-Za-z\s]+)"},
    "age": {"ner": ["DATE", "CARDINAL"], "keywords": ["age"], "regex": r"(?:i am|age[:\s]+)(\d{1,3})"},
    "country": {"ner": ["GPE"], "keywords": ["country", "from"], "regex": r"(?:from|country[:\s]+)([A-Za-z\s]+)"},
    "email": {"ner": [], "keywords": ["email"], "regex": r"[\w\.-]+@[\w\.-]+\.\w+"},
    "phone_number": {"ner": [], "keywords": ["phone","phone_number", "mobile"], "regex": r"\+?\d[\d\s\-]{7,15}"},
    
    # New fields
    "height": {"ner": ["QUANTITY", "CARDINAL"], "keywords": ["height"], "regex": r"(?:height[:\s]+)(\d{1,3}\s?(?:cm|m|in|ft))"},

    "weight": {"ner": ["QUANTITY", "CARDINAL"], "keywords": ["weight"], "regex": r"(?:weight[:\s]+)(\d{1,3}\s?(?:kg|lbs|lb))"},
    "color": {"ner": ["COLOR"], "keywords": ["color", "colour"], "regex": r"(?:color[:\s]+)([A-Za-z\s]+)"}
}

def extract_info(text):
    extracted = {}
    doc = nlp(text)

    # 1️⃣ NER based extraction
    for ent in doc.ents:
        for field, rules in FORM_FIELDS.items():
            if ent.label_ in rules["ner"] and field not in extracted:
                extracted[field] = ent.text.strip()

    # 2️⃣ Regex based extraction
    for field, rules in FORM_FIELDS.items():
        if field not in extracted and rules["regex"]:
            match = re.search(rules["regex"], text, re.IGNORECASE)
            if match:
                extracted[field] = match.group(1) if match.groups() else match.group(0)

    # 3️⃣ Default empty for missing fields
    for field in FORM_FIELDS:
        extracted.setdefault(field, "")

    return extracted

# ------------------------------------------------------------
# ✅ Index Route
# ------------------------------------------------------------
@app.route("/")
def index():
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT * FROM submissions1 ORDER BY id ASC")
    rows = cursor.fetchall()
    conn.close()
    return render_template("index.html", submissions1=rows)

# ------------------------------------------------------------
# ✅ PDF Upload
# ------------------------------------------------------------

@app.route("/upload_pdf", methods=["POST"])
def upload_pdf():
    if "pdf_file" not in request.files:
        return jsonify({"status": "error", "message": "No file uploaded"})

    file = request.files["pdf_file"]
    if not file.filename.endswith(".pdf"):
        return jsonify({"status": "error", "message": "Only PDF files allowed"})

    filepath = os.path.join(UPLOAD_FOLDER, file.filename)
    file.save(filepath)

    # Extract text from PDF
    text = ""
    with open(filepath, "rb") as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            text += page.extract_text() + "\n"

    # Dynamic extraction
    extracted_data = extract_info(text)

    return jsonify({"status": "success", "text": text.strip(), "data": extracted_data})


# ------------------------------------------------------------
# ✅ Submit / Update
# ------------------------------------------------------------
@app.route("/submit", methods=["POST"])
def submit():
    data = request.get_json()

    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()

    try:
        cursor.execute("SHOW COLUMNS FROM submissions1")
        existing = [c[0] for c in cursor.fetchall()]

        # ✅ Add missing dynamic columns
        for key in data.keys():
            if key not in existing and key != "id":
                cursor.execute(f"ALTER TABLE submissions1 ADD COLUMN `{key}` VARCHAR(255)")

        if "id" in data:   # ✅ UPDATE
            row_id = data.pop("id")
            updates = ", ".join([f"{k}=%s" for k in data.keys()])
            vals = list(data.values()) + [row_id]

            cursor.execute(f"UPDATE submissions1 SET {updates} WHERE id=%s", vals)

        else:      # ✅ INSERT
            cols = ", ".join(data.keys())
            placeholders = ", ".join(["%s"] * len(data))
            vals = list(data.values())

            cursor.execute(f"INSERT INTO submissions1 ({cols}) VALUES ({placeholders})", vals)

        conn.commit()
        return jsonify({"status": "success"})

    except Exception as e:
        return jsonify({"status": "error", "message": str(e)})

    finally:
        conn.close()

# ------------------------------------------------------------
# ✅ CSV Download
# ------------------------------------------------------------
@app.route("/download_csv")
def download_csv():
    conn = mysql.connector.connect(**db_config)
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM submissions1")
    rows = cursor.fetchall()
    col_names = [c[0] for c in cursor.description]
    conn.close()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(col_names)
    writer.writerows(rows)
    output.seek(0)

    return send_file(
        io.BytesIO(output.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name="submissions.csv"
    )

# ------------------------------------------------------------
# ✅ Run App
# ------------------------------------------------------------
if __name__ == "__main__":
    app.run(host="localhost", port=8000, debug=True)
