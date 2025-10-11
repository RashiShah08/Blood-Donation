from flask import Flask, render_template, redirect, url_for, request
import subprocess
import sys
import os
import time

app = Flask(__name__, template_folder="Template")
app.secret_key = os.urandom(24).hex()

# Start proximity.py as a subprocess
def start_proximity():
    try:
        # Ensure proximity.py exists
        if not os.path.exists("proximity.py"):
            print("Error: proximity.py not found")
            return

        # Run proximity.py as a subprocess
        cmd = [sys.executable, "proximity.py"]
        process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        print(f"Started proximity.py as subprocess (PID: {process.pid})")
        # Give the subprocess a moment to start
        time.sleep(2)
        return process
    except Exception as e:
        print(f"Failed to start proximity.py: {str(e)}")

@app.route("/")
def home():
    return render_template("home.html")

@app.route("/donor_login")
def donor_login():
    return render_template("donor_login.html")

@app.route("/donordashboard")
def donordashboard():
    return render_template("donordashboard.html")

@app.route("/hospitallogin")
def hospitallogin():
    return render_template("hospital_login.html")

@app.route("/hospitaldashboard")
def hospitaldashboard():
    return render_template("hospitaldashboard.html")

@app.route('/map.html', methods=['GET'])
def serve_map():
    request_id = request.args.get('requestId', None)
    return render_template('map.html', request_id=request_id)

@app.route('/proximity.html')
def proximity():
    return render_template('proximity.html')

@app.route('/about_us')
def aboutus():
    return render_template('about_us.html')

@app.route('/FAQ')
def FAQ():
    return render_template('FAQ.html')

@app.route('/for_donor')
def fordonor():
    return render_template('for_donor.html')

@app.route('/for_patients')
def for_patients():
    return render_template('for_patients.html')

@app.route('/howitworks')
def howitworks():
    return render_template('how_it_works.html')

@app.route('/terms_conditions')
def terms_conditions():
    return render_template('terms_conditions.html')

@app.route('/reward')
def reward():
    return render_template('reward.html')

if __name__ == "__main__":
    # Start proximity.py subprocess
    proximity_process = start_proximity()
    try:
        # Run the main Flask app
        app.run(host="127.0.0.1", port=5000, debug=True)
    finally:
        # Terminate the subprocess when the main app stops
        if proximity_process:
            print("Terminating proximity.py subprocess")
            proximity_process.terminate()
            proximity_process.wait()