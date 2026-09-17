"""Run BloodConnect locally: `python app.py` (or `flask --app app run`)."""

import os

from bloodconnect import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host=os.getenv("HOST", "127.0.0.1"), port=int(os.getenv("PORT", "5000")), debug=app.debug)
