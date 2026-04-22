from flask import Flask

app = Flask(__name__)

@app.route("/")
def index():
    return """
    <html>
        <body>
            <h2>Camera Feed</h2>
            <img src="http://127.0.0.1:8000" />
        </body>
    </html>
    """

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000)
