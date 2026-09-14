
import os
from flask import Flask, render_template, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# Load environment variables
env_file = ".env"  # Copy .env.example to .env and fill in your values
if not os.path.exists(env_file):
    raise FileNotFoundError(f"Environment file {env_file} not found (copy .env.example to .env)")

load_dotenv(env_file)

# Validate environment variables
required_env_vars = ["SMTP_HOST", "EMAIL_ADDRESS", "EMAIL_PASSWORD"]
missing_vars = [var for var in required_env_vars if not os.getenv(var)]
if missing_vars:
    raise ValueError(f"Missing environment variables: {', '.join(missing_vars)}")

try:
    SMTP_PORT = int(os.getenv("SMTP_PORT", "465"))
    if SMTP_PORT <= 0 or SMTP_PORT > 65535:
        raise ValueError("SMTP_PORT must be a valid port number")
except ValueError as e:
    raise ValueError(f"Invalid SMTP_PORT: {str(e)}")

# Flask app
app = Flask(__name__, template_folder="templates")
app.secret_key = os.getenv("FLASK_SECRET_KEY", os.urandom(24).hex())  # Secure default
CORS(app)  # Enable CORS for frontend requests

# Email credentials
SMTP_HOST = os.getenv("SMTP_HOST")
SMTP_PORT = SMTP_PORT
EMAIL_ADDRESS = os.getenv("EMAIL_ADDRESS")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")

# --- Email Function ---
def send_email(to_email, subject, body):
    try:
        # Validate email format (basic check)
        if not isinstance(to_email, str) or "@" not in to_email:
            return {"success": False, "error": f"Invalid email address: {to_email}"}

        msg = MIMEMultipart()
        msg["From"] = EMAIL_ADDRESS
        msg["To"] = to_email
        msg["Subject"] = subject
        msg.attach(MIMEText(body, "plain"))

        with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT) as server:
            server.login(EMAIL_ADDRESS, EMAIL_PASSWORD)
            server.sendmail(EMAIL_ADDRESS, to_email, msg.as_string())

        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}

# --- Routes ---
@app.route("/")
def index():
    return render_template("proximity.html")

@app.route("/send", methods=["POST"])
def send():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"success": False, "error": "No JSON data provided"}), 400

        print("Received JSON:", data)

        emails = data.get("emails", [])
        message = data.get("message", "Urgent! Blood donation required.")
        hospital = data.get("hospital", "Unknown Hospital")

        if not isinstance(emails, list) or not emails:
            return jsonify({"success": False, "error": "No valid recipient emails provided"}), 400
        if not isinstance(message, str) or not message.strip():
            return jsonify({"success": False, "error": "Message cannot be empty"}), 400
        if not isinstance(hospital, str) or not hospital.strip():
            hospital = "Unknown Hospital"  # Fallback

        subject = f"Urgent Blood Donation Request - {hospital}"
        results = {}

        for email in emails:
            results[email] = send_email(email, subject, message)

        return jsonify({"success": True, "results": results})
    except Exception as e:
        return jsonify({"success": False, "error": f"Server error: {str(e)}"}), 500

# --- Main ---
if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5001)  # Debug=False for safety
