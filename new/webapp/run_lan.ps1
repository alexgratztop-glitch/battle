$ErrorActionPreference = "Stop"

# Run the webapp so other devices on the same Wi‑Fi/LAN can open it.
# Then open on phone: http://<PC_LAN_IP>:8000

$env:PYTHONPATH = "d:\spacebattle\new"
python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

