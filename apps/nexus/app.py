from flask import Flask, render_template, request

app = Flask(__name__)

@app.route("/")
def index():
    return """
    <h2>Available pages</h2>
    <ul>
        <li><a href="/label">LabelStudio</a></li>
        <li><a href="/dashboard">MLFlow</a></li>
    </ul>
    """

@app.route("/label")
def label():
    print(request.endpoint)
    return render_template("label-studio.html")

@app.route("/dashboard")
def dashboard():
    return render_template("mlflow.html")