"""Car Rental POC — shared scaffold service.

One image, run under different SERVICE_NAME values (see architecture doc LLD
02 note). Ships enough wiring to be real and testable end to end; the actual
business logic per service is intentionally not yet written — this is the
documented POC simplification, not an oversight.
"""
import json
import os

from flask import Flask, jsonify, request

app = Flask(__name__)

SERVICE_NAME = os.environ.get("SERVICE_NAME", "generic-svc")

DEFAULT_SEED = {
    "catalog-svc": [
        {"make": "TATA", "model": "Harrier", "rate_per_km": 14.00},
        {"make": "Mahindra", "model": "XUV700", "rate_per_km": 15.50},
        {"make": "Honda", "model": "City", "rate_per_km": 11.00},
        {"make": "Force", "model": "Traveller", "rate_per_km": 18.00},
        {"make": "Maruti", "model": "Ertiga", "rate_per_km": 12.50},
    ],
    "location-svc": [
        {"name": "Bengaluru"}, {"name": "Chennai"}, {"name": "Hyderabad"},
        {"name": "Mumbai"}, {"name": "Delhi"},
    ],
}

_seed_env = os.environ.get("SEED_DATA_JSON")
SEED_DATA = json.loads(_seed_env) if _seed_env else DEFAULT_SEED.get(SERVICE_NAME, [])


@app.get("/health")
def health():
    return jsonify(status="ok", service=SERVICE_NAME)


@app.get("/items")
def items():
    return jsonify(SEED_DATA)


@app.get("/")
def root():
    return jsonify(
        service=SERVICE_NAME,
        note="POC scaffold — business logic not yet implemented for this service",
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 8080)))
