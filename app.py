from flask import Flask, request, jsonify, send_from_directory, session, redirect
import os
import sqlite3
import hmac
import hashlib
import requests as http_requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), '.env'))

app = Flask(__name__, static_folder='static')
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key-change-this")

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

RAZORPAY_KEY_ID     = os.getenv("RAZORPAY_KEY_ID", "")
RAZORPAY_KEY_SECRET = os.getenv("RAZORPAY_KEY_SECRET", "")
PRICE_INR_PAISE     = 74900   # ₹749 (≈ $9)
FREE_LIMIT          = 1

# ---------- Database ----------
def init_db():
    conn = sqlite3.connect('usage.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS paid_sessions
                 (session_id TEXT PRIMARY KEY, paid_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP)''')
    conn.commit()
    conn.close()

def is_paid(session_id):
    conn = sqlite3.connect('usage.db')
    c = conn.cursor()
    c.execute("SELECT 1 FROM paid_sessions WHERE session_id = ?", (session_id,))
    result = c.fetchone()
    conn.close()
    return result is not None

def mark_paid(session_id):
    conn = sqlite3.connect('usage.db')
    c = conn.cursor()
    c.execute("INSERT OR IGNORE INTO paid_sessions (session_id) VALUES (?)", (session_id,))
    conn.commit()
    conn.close()

init_db()

# ---------- Routes ----------
@app.route('/')
def index():
    if 'user_id' not in session:
        import uuid
        session['user_id'] = str(uuid.uuid4())
    if 'resume_count' not in session:
        session['resume_count'] = 0
    return send_from_directory('static', 'index.html')

@app.route('/status')
def status():
    user_id = session.get('user_id', '')
    count   = session.get('resume_count', 0)
    paid    = is_paid(user_id)
    return jsonify({
        "paid": paid,
        "resume_count": count,
        "free_limit": FREE_LIMIT,
        "can_generate": paid or count < FREE_LIMIT,
        "razorpay_key_id": RAZORPAY_KEY_ID
    })

@app.route('/generate-resume', methods=['POST'])
def generate_resume():
    user_id = session.get('user_id', '')
    count   = session.get('resume_count', 0)
    paid    = is_paid(user_id)

    if not paid and count >= FREE_LIMIT:
        return jsonify({"error": "free_limit_reached"}), 403

    data       = request.json
    name       = data.get('name', '')
    email      = data.get('email', '')
    phone      = data.get('phone', '')
    experience = data.get('experience', '')
    education  = data.get('education', '')
    skills     = data.get('skills', '')
    job_title  = data.get('job_title', '')

    prompt = f"""Create a professional resume for the following person. Format it cleanly with clear sections.

Name: {name}
Email: {email}
Phone: {phone}
Target Job Title: {job_title}

Work Experience:
{experience}

Education:
{education}

Skills:
{skills}

Generate a polished, ATS-friendly resume with:
- Professional summary
- Work Experience (with bullet points highlighting achievements)
- Education
- Skills section
- Keep it concise and impactful
"""

    resp = http_requests.post(
        f"{GEMINI_URL}?key={GEMINI_API_KEY}",
        json={"contents": [{"parts": [{"text": prompt}]}]},
        timeout=60
    )
    if not resp.ok:
        return jsonify({"error": f"AI error: {resp.status_code} {resp.text[:200]}"}), 500
    data = resp.json()
    parts = data["candidates"][0]["content"]["parts"]
    resume_text = next((p["text"] for p in parts if not p.get("thought")), parts[-1]["text"])
    session['resume_count'] = count + 1
    return jsonify({"resume": resume_text, "paid": paid})

# ---------- Razorpay Payment ----------
@app.route('/create-order', methods=['POST'])
def create_order():
    import uuid
    order_data = {
        "amount": PRICE_INR_PAISE,
        "currency": "INR",
        "receipt": str(uuid.uuid4())[:20],
        "notes": {"user_id": session.get('user_id', '')}
    }
    response = http_requests.post(
        "https://api.razorpay.com/v1/orders",
        json=order_data,
        auth=(RAZORPAY_KEY_ID, RAZORPAY_KEY_SECRET)
    )
    if response.status_code == 200:
        return jsonify(response.json())
    else:
        return jsonify({"error": "Failed to create order"}), 500

@app.route('/verify-payment', methods=['POST'])
def verify_payment():
    data               = request.json
    razorpay_order_id  = data.get('razorpay_order_id', '')
    razorpay_payment_id = data.get('razorpay_payment_id', '')
    razorpay_signature  = data.get('razorpay_signature', '')

    # Verify signature
    message = f"{razorpay_order_id}|{razorpay_payment_id}"
    expected = hmac.new(
        RAZORPAY_KEY_SECRET.encode(),
        message.encode(),
        hashlib.sha256
    ).hexdigest()

    if hmac.compare_digest(expected, razorpay_signature):
        user_id = session.get('user_id', '')
        mark_paid(user_id)
        return jsonify({"success": True})
    else:
        return jsonify({"success": False, "error": "Invalid signature"}), 400

@app.route('/success')
def success():
    return send_from_directory('static', 'success.html')

if __name__ == '__main__':
    print("AI Resume Builder running at http://localhost:5000")
    app.run(debug=True, port=5000)
