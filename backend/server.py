from flask import Flask, jsonify
from flask_cors import CORS

app = Flask(__name__)
CORS(app)


@app.get("/")
def home():
    return jsonify({
        "status": "online",
        "message": "MP Tenders API is running"
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "healthy"
    })


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=10000)
