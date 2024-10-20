import os
from flask import Flask, render_template, request, jsonify, session
from werkzeug.utils import secure_filename
from PyPDF2 import PdfReader
from flask_session import Session
from dotenv import load_dotenv
from groq import Groq

app = Flask(__name__)

# Set upload folder and allowed file types
app.config["UPLOAD_FOLDER"] = "uploads/"
app.config["ALLOWED_EXTENSIONS"] = {"pdf"}

# Configure session to use filesystem
app.config["SECRET_KEY"] = (
    "b'\xf1\xc9\xd2\xab\x86\xf7\xd0\x87\x12\x9c\xd1\x8e\x93\xe3\xff\xc4\x84\x98\xa3\xe4\xf7\xd4'"
)
app.config["SESSION_TYPE"] = "filesystem"
Session(app)

# Load .env file
load_dotenv()

# Initialize the Groq client
api_key = os.getenv("GROQ_API_KEY")
client = Groq(api_key=api_key)


# Function to check if the file is allowed
def allowed_file(filename):
    return (
        "." in filename
        and filename.rsplit(".", 1)[1].lower() in app.config["ALLOWED_EXTENSIONS"]
    )


# Home page
@app.route("/")
def index():
    return render_template("pdf_chat.html")


# Route to handle file upload and PDF text extraction
@app.route("/upload", methods=["POST"])
def upload_file():
    if "file" not in request.files:
        return jsonify({"error": "No file part"})

    file = request.files["file"]

    if file.filename == "":
        return jsonify({"error": "No selected file"})

    if file and allowed_file(file.filename):
        filename = secure_filename(file.filename)
        file_path = os.path.join(app.config["UPLOAD_FOLDER"], filename)
        file.save(file_path)

        # Extract text from the PDF
        extracted_text = extract_text_from_pdf(file_path)

        # Store extracted text in the session
        session["pdf_text"] = extracted_text
        return jsonify(
            {
                "message": "PDF uploaded successfully!",
                "text": extracted_text[:500] + "...",  # Preview first 500 characters
            }
        )

    return jsonify({"error": "File type not allowed"})


# Function to extract text from PDF
def extract_text_from_pdf(pdf_path):
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text()
    return text


# Route to handle chat interaction
@app.route("/ask", methods=["POST"])
def ask():
    query = request.form["query"]
    pdf_text = session.get("pdf_text", "")

    if not pdf_text:
        return jsonify({"error": "No PDF content to query"})

    # Combine the query and the extracted PDF text for Llama model to process
    prompt = (
        f"Based on the following document: {pdf_text}\n\nAnswer this question: {query}"
    )

    # Send query to Groq Llama model
    response = query_llama(prompt)
    return jsonify(response)


# Function to query Llama model via Groq API
def query_llama(text):
    try:
        chat_completion = client.chat.completions.create(
            messages=[
                {
                    "role": "user",
                    "content": text,
                }
            ],
            model="llama3-70b-8192",  # Replace with the appropriate model from Groq
        )
        return {"response": chat_completion.choices[0].message.content}
    except Exception as e:
        return {"error": str(e)}


if __name__ == "__main__":
    if not os.path.exists(app.config["UPLOAD_FOLDER"]):
        os.makedirs(app.config["UPLOAD_FOLDER"])
    app.run(host="192.168.29.55", port=5000, debug=True)
