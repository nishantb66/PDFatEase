import os
import json
import zipfile
from flask import (
    Blueprint,
    render_template,
    request,
    send_file,
    redirect,
    url_for,
    flash,
    current_app,
    jsonify,
    session,
)
from werkzeug.utils import secure_filename
from PyPDF2 import PdfReader, PdfWriter
from reportlab.lib.pagesizes import letter
from io import BytesIO
from datetime import datetime
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.colors import gray
import pdfplumber
import fitz
from pdf2docx import Converter
import pdfplumber
import requests
import groq
import pyttsx3  # For text-to-speech
from flask import send_from_directory


main = Blueprint("main", __name__)


@main.route("/", methods=["GET"])
def welcome():
    return render_template("welcome.html")


# Set the allowed file extension and max files to 4
ALLOWED_EXTENSIONS = {"pdf", "png", "jpg", "jpeg"}
MAX_FILES = 4


def allowed_file(filename):
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_EXTENSIONS


def watermark_pdf(pdf_path, watermark_text):
    # Create a temporary watermark PDF in memory
    watermark_stream = BytesIO()
    c = canvas.Canvas(watermark_stream, pagesize=letter)

    # Set the font and color for the watermark
    c.setFont("Helvetica-Bold", 50)
    c.setFillColor(gray, alpha=0.1)  # Light gray, semi-transparent

    # Rotate and position the watermark diagonally
    c.saveState()
    c.translate(300, 500)  # Translate the origin to the center of the page
    c.rotate(45)  # Rotate by 45 degrees
    c.drawCentredString(0, 0, watermark_text)
    c.restoreState()

    # Finalize the watermark PDF
    c.save()

    # Move to the beginning of the stream
    watermark_stream.seek(0)

    # Read the original PDF
    reader = PdfReader(pdf_path)
    writer = PdfWriter()

    # Read the watermark
    watermark_reader = PdfReader(watermark_stream)
    watermark_page = watermark_reader.pages[0]

    # Apply watermark to each page of the original PDF
    for page in reader.pages:
        page.merge_page(watermark_page)
        writer.add_page(page)

    # Create the new watermarked PDF path
    watermarked_filename = os.path.basename(pdf_path).replace(
        ".pdf", "_watermarked.pdf"
    )
    watermarked_pdf_path = os.path.join(
        current_app.config["UPLOAD_FOLDER"], watermarked_filename
    )

    # Save the watermarked PDF
    with open(watermarked_pdf_path, "wb") as output_file:
        writer.write(output_file)

    return watermarked_pdf_path


@main.route("/index", methods=["GET", "POST"])
def index():
    processed_files = []  # List to hold paths of processed files (encrypted/decrypted)
    action = request.form.get("action")  # Encrypt, decrypt, or watermark
    watermark_text = request.form.get("watermark_text", "")  # Get the watermark text
    expiration_date_str = request.form.get("expiration_date")  # Get the expiration date
    expiration_time_str = request.form.get("expiration_time")  # Get the expiration time
    expired = False  # Flag to indicate whether the decryption has expired

    if request.method == "POST":
        files = request.files.getlist("files")  # Multiple file handling
        password = request.form["password"]

        if len(files) > MAX_FILES:
            flash("You can upload a maximum of 4 files.", "error")
            return redirect(request.url)

        if not password and action != "watermark":  # Watermark doesn't require password
            flash("Please provide a password.", "error")
            return redirect(request.url)

        # Parse the expiration date and time if provided
        expiration_datetime = None
        if expiration_date_str and expiration_time_str:
            try:
                expiration_datetime = datetime.strptime(
                    f"{expiration_date_str} {expiration_time_str}", "%Y-%m-%d %H:%M"
                )
            except ValueError:
                flash("Invalid expiration date or time format.", "error")
                return redirect(request.url)

        # Create the upload directory if not exists
        upload_folder = current_app.config["UPLOAD_FOLDER"]
        if not os.path.exists(upload_folder):
            os.makedirs(upload_folder)

        for pdf_file in files:
            if pdf_file and allowed_file(pdf_file.filename):
                filename = secure_filename(pdf_file.filename)
                upload_path = os.path.join(upload_folder, filename)
                pdf_file.save(upload_path)

                if action == "encrypt":
                    # Encrypt the PDF with optional expiration datetime
                    encrypted_file_path = encrypt_pdf(
                        upload_path, password, expiration_datetime
                    )
                    processed_files.append(encrypted_file_path)
                elif action == "decrypt":
                    # Attempt to decrypt the PDF
                    decrypted_file_path = decrypt_pdf(upload_path, password)
                    if decrypted_file_path is None:
                        expired = True  # Set the flag to trigger the alert
                    else:
                        processed_files.append(decrypted_file_path)
                elif action == "watermark" and watermark_text:
                    # Apply watermark to the PDF
                    watermarked_file_path = watermark_pdf(upload_path, watermark_text)
                    processed_files.append(watermarked_file_path)

        # If multiple files, offer download as zip archive
        if len(processed_files) > 1:
            zip_file_path = create_zip_archive(processed_files, upload_folder)
            return send_file(
                zip_file_path, as_attachment=True, download_name="processed_files.zip"
            )
        elif processed_files:
            return send_file(processed_files[0], as_attachment=True)

    # Render the template with the expired flag if decryption failed
    return render_template("index.html", expired=expired)


# Helper function to encrypt a PDF file with expiration date and time
def encrypt_pdf(pdf_path, password, expiration_datetime=None):
    reader = PdfReader(pdf_path)
    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    writer.encrypt(password)

    encrypted_filename = os.path.basename(pdf_path).replace(".pdf", "_encrypted.pdf")
    encrypted_pdf_path = os.path.join(
        current_app.config["UPLOAD_FOLDER"], encrypted_filename
    )

    # Save the encrypted PDF
    with open(encrypted_pdf_path, "wb") as output_file:
        writer.write(output_file)

    # Save the expiration date and time in a separate metadata file (JSON)
    if expiration_datetime:
        metadata = {
            "expiration_datetime": expiration_datetime.strftime("%Y-%m-%d %H:%M:%S")
        }
        metadata_filename = encrypted_filename.replace(".pdf", "_metadata.json")
        metadata_path = os.path.join(
            current_app.config["UPLOAD_FOLDER"], metadata_filename
        )
        with open(metadata_path, "w") as metadata_file:
            json.dump(metadata, metadata_file)

    return encrypted_pdf_path


# Helper function to decrypt a PDF file
def decrypt_pdf(pdf_path, password):
    # Check if the file has expired
    metadata_filename = os.path.basename(pdf_path).replace(".pdf", "_metadata.json")
    metadata_path = os.path.join(current_app.config["UPLOAD_FOLDER"], metadata_filename)

    if os.path.exists(metadata_path):
        with open(metadata_path, "r") as metadata_file:
            metadata = json.load(metadata_file)
            expiration_datetime_str = metadata.get("expiration_datetime")
            if expiration_datetime_str:
                expiration_datetime = datetime.strptime(
                    expiration_datetime_str, "%Y-%m-%d %H:%M:%S"
                )
                if datetime.now() > expiration_datetime:
                    return None  # Return None if the file has expired

    # Proceed with decryption
    reader = PdfReader(pdf_path)

    if reader.is_encrypted:
        try:
            reader.decrypt(password)
        except Exception:
            raise Exception("Decryption failed due to incorrect password.")

    # Write the decrypted PDF to a new file
    decrypted_filename = os.path.basename(pdf_path).replace(".pdf", "_decrypted.pdf")
    decrypted_pdf_path = os.path.join(
        current_app.config["UPLOAD_FOLDER"], decrypted_filename
    )

    writer = PdfWriter()
    for page in reader.pages:
        writer.add_page(page)

    with open(decrypted_pdf_path, "wb") as output_file:
        writer.write(output_file)

    return decrypted_pdf_path


# New route to handle watermarking PDFs
@main.route("/watermark", methods=["POST"])
def watermark():
    processed_files = []
    watermark_text = request.form.get("watermark_text", "")
    files = request.files.getlist("files")

    if len(files) > MAX_FILES:
        flash("You can upload a maximum of 4 files.", "error")
        return redirect(request.url)

    if not watermark_text:
        flash("Please provide a watermark text.", "error")
        return redirect(request.url)

    # Create the upload directory if not exists
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)

    for pdf_file in files:
        if pdf_file and allowed_file(pdf_file.filename):
            filename = secure_filename(pdf_file.filename)
            upload_path = os.path.join(upload_folder, filename)
            pdf_file.save(upload_path)

            # Apply watermark to the PDF
            watermarked_file_path = watermark_pdf(upload_path, watermark_text)
            processed_files.append(watermarked_file_path)

    # If multiple files, offer download as zip archive
    if len(processed_files) > 1:
        zip_file_path = create_zip_archive(processed_files, upload_folder)
        return send_file(
            zip_file_path, as_attachment=True, download_name="watermarked_files.zip"
        )
    elif processed_files:
        return send_file(processed_files[0], as_attachment=True)

    return redirect(url_for("main.index"))


# Helper function to create a zip archive of files
def create_zip_archive(file_paths, output_dir):
    zip_filename = os.path.join(output_dir, "processed_files.zip")
    with zipfile.ZipFile(zip_filename, "w") as zipf:
        for file in file_paths:
            zipf.write(file, os.path.basename(file))
    return zip_filename


@main.route("/download/<filename>")
def download_file(filename):
    file_path = os.path.join(current_app.config["UPLOAD_FOLDER"], filename)
    return send_file(file_path, as_attachment=True)


def apply_signature_to_pdf(pdf_path, signature_image_path, signature_text):
    reader = PdfReader(pdf_path)
    writer = PdfWriter()

    # Create the signature overlay (in memory)
    signature_overlay = BytesIO()
    c = canvas.Canvas(signature_overlay, pagesize=letter)

    if signature_image_path:
        c.drawImage(signature_image_path, 100, 50, width=150, height=50)
    else:
        # Add text-based signature if no image
        c.setFont("Helvetica", 12)
        c.drawString(100, 50, f"Digitally Signed by: {signature_text}")

    c.save()
    signature_overlay.seek(0)

    # Merge the signature with each page of the PDF
    overlay_pdf = PdfReader(signature_overlay)
    overlay_page = overlay_pdf.pages[0]

    for page in reader.pages:
        page.merge_page(overlay_page)
        writer.add_page(page)

    signed_pdf_filename = os.path.basename(pdf_path).replace(".pdf", "_signed.pdf")
    signed_pdf_path = os.path.join(
        current_app.config["UPLOAD_FOLDER"], signed_pdf_filename
    )

    # Write the signed PDF to disk
    with open(signed_pdf_path, "wb") as f:
        writer.write(f)

    return signed_pdf_path


@main.route("/sign", methods=["POST"])
def sign_pdf():
    processed_files = []
    signature_text = request.form.get("signature_text", "")
    files = request.files.getlist("files")
    signature_image = request.files.get("signature_image")

    if len(files) > MAX_FILES:
        flash("You can upload a maximum of 4 files.", "error")
        return redirect(request.url)

    # Create the upload directory if not exists
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)

    # Save the uploaded signature image (if provided)
    signature_image_path = None
    if signature_image and allowed_file(signature_image.filename):
        signature_filename = secure_filename(signature_image.filename)
        signature_image_path = os.path.join(upload_folder, signature_filename)
        signature_image.save(signature_image_path)

    for pdf_file in files:
        if pdf_file and allowed_file(pdf_file.filename):
            filename = secure_filename(pdf_file.filename)
            upload_path = os.path.join(upload_folder, filename)
            pdf_file.save(upload_path)

            # Apply signature (image or text-based)
            signed_file_path = apply_signature_to_pdf(
                upload_path, signature_image_path, signature_text
            )
            processed_files.append(signed_file_path)

    if len(processed_files) > 1:
        zip_file_path = create_zip_archive(processed_files, upload_folder)
        return send_file(
            zip_file_path, as_attachment=True, download_name="signed_files.zip"
        )
    elif processed_files:
        return send_file(processed_files[0], as_attachment=True)

    return redirect("/")


# Function to merge multiple PDFs
def merge_pdfs(pdf_paths):
    writer = PdfWriter()

    for pdf in pdf_paths:
        reader = PdfReader(pdf)
        for page in reader.pages:
            writer.add_page(page)

    merged_pdf_path = os.path.join(
        current_app.config["UPLOAD_FOLDER"], "merged_output.pdf"
    )

    with open(merged_pdf_path, "wb") as output_file:
        writer.write(output_file)

    return merged_pdf_path


# Function to compress PDF (by optimizing its content)
def compress_pdf(pdf_path):
    reader = PdfReader(pdf_path)
    writer = PdfWriter()

    for page in reader.pages:
        writer.add_page(page)

    compressed_filename = os.path.basename(pdf_path).replace(".pdf", "_compressed.pdf")
    compressed_pdf_path = os.path.join(
        current_app.config["UPLOAD_FOLDER"], compressed_filename
    )

    with open(compressed_pdf_path, "wb") as output_file:
        writer.write(output_file)

    return compressed_pdf_path


# Main route for compression and merging PDFs
@main.route("/compress", methods=["POST"])
def compress_or_merge():
    processed_files = []
    action = request.form.get("compress_action")  # Compress or merge
    files = request.files.getlist("files")

    if len(files) > MAX_FILES:
        flash("You can upload a maximum of 4 files.", "error")
        return redirect(request.url)

    upload_folder = current_app.config["UPLOAD_FOLDER"]
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)

    pdf_paths = []

    for pdf_file in files:
        if pdf_file and allowed_file(pdf_file.filename):
            filename = secure_filename(pdf_file.filename)
            upload_path = os.path.join(upload_folder, filename)
            pdf_file.save(upload_path)
            pdf_paths.append(upload_path)

            if action == "compress":
                # Compress the PDF
                compressed_file_path = compress_pdf(upload_path)
                processed_files.append(compressed_file_path)

    if action == "merge":
        if len(pdf_paths) > 1:
            # Merge PDFs
            merged_file_path = merge_pdfs(pdf_paths)
            processed_files.append(merged_file_path)
        else:
            flash("Please upload at least two PDFs to merge.", "error")
            return redirect(request.url)

    # If multiple files, offer download as zip archive
    if len(processed_files) > 1:
        zip_file_path = create_zip_archive(processed_files, upload_folder)
        return send_file(
            zip_file_path, as_attachment=True, download_name="processed_files.zip"
        )
    elif processed_files:
        return send_file(processed_files[0], as_attachment=True)

    return redirect(url_for("main.index"))


# Helper function to split a PDF based on page numbers
def split_pdf_at_pages(pdf_path, split_pages):
    reader = PdfReader(pdf_path)
    split_pages = sorted(
        set([int(page) for page in split_pages])
    )  # Sort and remove duplicates

    total_pages = len(reader.pages)
    split_points = [0] + split_pages + [total_pages]

    output_files = []
    for i in range(len(split_points) - 1):
        writer = PdfWriter()
        for page in range(split_points[i], split_points[i + 1]):
            writer.add_page(reader.pages[page])

        split_filename = os.path.basename(pdf_path).replace(".pdf", f"_part{i+1}.pdf")
        split_pdf_path = os.path.join(
            current_app.config["UPLOAD_FOLDER"], split_filename
        )

        with open(split_pdf_path, "wb") as output_file:
            writer.write(output_file)

        output_files.append(split_pdf_path)

    return output_files


# Route to handle PDF splitting
@main.route("/split", methods=["POST"])
def split_pdf():
    processed_files = []
    split_pages = request.form.get("split_pages", "").split(",")
    files = request.files.getlist("files")

    if len(files) > MAX_FILES:
        flash("You can upload a maximum of 4 files.", "error")
        return redirect(request.url)

    if not split_pages:
        flash("Please provide the page numbers to split the PDF.", "error")
        return redirect(request.url)

    # Create the upload directory if not exists
    upload_folder = current_app.config["UPLOAD_FOLDER"]
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)

    for pdf_file in files:
        if pdf_file and allowed_file(pdf_file.filename):
            filename = secure_filename(pdf_file.filename)
            upload_path = os.path.join(upload_folder, filename)
            pdf_file.save(upload_path)

            # Split the PDF at the given page numbers
            split_files = split_pdf_at_pages(upload_path, split_pages)
            processed_files.extend(split_files)

    # If multiple files, offer download as zip archive
    if len(processed_files) > 1:
        zip_file_path = create_zip_archive(processed_files, upload_folder)
        return send_file(
            zip_file_path, as_attachment=True, download_name="split_files.zip"
        )
    elif processed_files:
        return send_file(processed_files[0], as_attachment=True)

    return redirect(url_for("main.index"))


# Add this function to remove specific pages from the PDF
def delete_pages_from_pdf(pdf_path, pages_to_delete):
    reader = PdfReader(pdf_path)
    writer = PdfWriter()

    total_pages = len(reader.pages)
    pages_to_delete = set(pages_to_delete)  # Ensure unique pages are deleted

    # Add pages that are not in the delete list
    for i in range(total_pages):
        if i + 1 not in pages_to_delete:  # Pages are 1-indexed for users
            writer.add_page(reader.pages[i])

    updated_pdf_filename = os.path.basename(pdf_path).replace(".pdf", "_updated.pdf")
    updated_pdf_path = os.path.join(
        current_app.config["UPLOAD_FOLDER"], updated_pdf_filename
    )

    # Write the updated PDF
    with open(updated_pdf_path, "wb") as output_file:
        writer.write(output_file)

    return updated_pdf_path


# Route to handle page deletion from a PDF
@main.route("/delete_pages", methods=["POST"])
def delete_pages():
    files = request.files.getlist("files")
    pages_to_delete = request.form.get("pages_to_delete")
    processed_files = []

    if len(files) > MAX_FILES:
        flash("You can upload a maximum of 4 files.", "error")
        return redirect(request.url)

    if not pages_to_delete:
        flash("Please provide page numbers to delete.", "error")
        return redirect(request.url)

    try:
        pages_to_delete = [int(p.strip()) for p in pages_to_delete.split(",")]
    except ValueError:
        flash("Please enter valid page numbers.", "error")
        return redirect(request.url)

    upload_folder = current_app.config["UPLOAD_FOLDER"]
    if not os.path.exists(upload_folder):
        os.makedirs(upload_folder)

    for pdf_file in files:
        if pdf_file and allowed_file(pdf_file.filename):
            filename = secure_filename(pdf_file.filename)
            upload_path = os.path.join(upload_folder, filename)
            pdf_file.save(upload_path)

            # Delete specified pages from the PDF
            updated_file_path = delete_pages_from_pdf(upload_path, pages_to_delete)
            processed_files.append(updated_file_path)

    # If multiple files, offer download as zip archive
    if len(processed_files) > 1:
        zip_file_path = create_zip_archive(processed_files, upload_folder)
        return send_file(
            zip_file_path, as_attachment=True, download_name="updated_files.zip"
        )
    elif processed_files:
        return send_file(processed_files[0], as_attachment=True)

    return redirect(url_for("main.index"))


# Route to handle PDF to Word conversion
@main.route("/convert_pdf_to_word", methods=["POST"])
def convert_pdf_to_word():
    if request.method == "POST":
        files = request.files.getlist("files")
        if len(files) > MAX_FILES:
            flash("You can upload a maximum of 4 files.", "error")
            return redirect(request.url)

        upload_folder = current_app.config["UPLOAD_FOLDER"]
        if not os.path.exists(upload_folder):
            os.makedirs(upload_folder)

        processed_files = []

        for pdf_file in files:
            if pdf_file and allowed_file(pdf_file.filename):
                filename = secure_filename(pdf_file.filename)
                upload_path = os.path.join(upload_folder, filename)
                pdf_file.save(upload_path)

                # Convert PDF to Word
                word_filename = filename.replace(".pdf", ".docx")
                word_file_path = os.path.join(upload_folder, word_filename)
                cv = Converter(upload_path)
                cv.convert(word_file_path, start=0, end=None)  # Convert the entire PDF
                cv.close()

                processed_files.append(word_file_path)

        # If multiple files, offer download as zip archive
        if len(processed_files) > 1:
            zip_file_path = create_zip_archive(processed_files, upload_folder)
            return send_file(
                zip_file_path, as_attachment=True, download_name="converted_files.zip"
            )
        elif processed_files:
            return send_file(processed_files[0], as_attachment=True)

    return redirect(url_for("main.index"))


# Helper function to add page numbers to PDF
def add_page_numbers(pdf_path):
    reader = PdfReader(pdf_path)
    writer = PdfWriter()

    # Create a temporary PDF with page numbers
    for page_num, page in enumerate(reader.pages, start=1):
        packet = BytesIO()
        can = canvas.Canvas(packet, pagesize=letter)

        # Add page number to bottom-right corner
        text = f"{page_num}"
        can.drawString(520, 20, text)
        can.save()

        # Move to the beginning of the StringIO buffer
        packet.seek(0)
        new_pdf = PdfReader(packet)

        # Merge page number onto the existing page
        page.merge_page(new_pdf.pages[0])
        writer.add_page(page)

    # Save the new PDF with page numbers
    output_filename = os.path.basename(pdf_path).replace(".pdf", "_numbered.pdf")
    output_pdf_path = os.path.join(current_app.config["UPLOAD_FOLDER"], output_filename)
    with open(output_pdf_path, "wb") as output_file:
        writer.write(output_file)

    return output_pdf_path


# Route to handle PDF upload and adding page numbers
@main.route("/add_page_numbers", methods=["POST"])
def add_page_numbers_route():
    if "file" not in request.files:
        return jsonify({"error": "No file uploaded"}), 400

    pdf_file = request.files["file"]

    if pdf_file and allowed_file(pdf_file.filename):
        filename = secure_filename(pdf_file.filename)
        upload_folder = current_app.config["UPLOAD_FOLDER"]
        pdf_path = os.path.join(upload_folder, filename)
        pdf_file.save(pdf_path)

        # Add page numbers to the PDF
        updated_pdf_path = add_page_numbers(pdf_path)

        return send_file(updated_pdf_path, as_attachment=True)

    return jsonify({"error": "Invalid file type"}), 400


# Route for "Hear a PDF"
@main.route("/hear_pdf", methods=["GET", "POST"])
def hear_pdf():
    if request.method == "POST":
        pdf_file = request.files.get("file")

        if pdf_file and allowed_file(pdf_file.filename):
            filename = secure_filename(pdf_file.filename)
            upload_folder = current_app.config["UPLOAD_FOLDER"]
            pdf_path = os.path.join(upload_folder, filename)
            pdf_file.save(pdf_path)

            # Extract text from the PDF
            text_content = extract_text_from_pdf(pdf_path)
            lines = text_content.split("\n")  # Split the text into lines

            # Prepare to read out loud
            audio_filename = generate_audio_from_text(lines)

            # Send the extracted text and audio file to the client for navigation
            return render_template(
                "hear_pdf.html", lines=lines, audio_file=audio_filename
            )

    return render_template("upload_pdf_for_reading.html")


def extract_text_from_pdf(pdf_path):
    """Extracts text from a PDF using pdfplumber"""
    with pdfplumber.open(pdf_path) as pdf:
        full_text = ""
        for page in pdf.pages:
            full_text += page.extract_text() + "\n"
    return full_text


def generate_audio_from_text(lines):
    """Generates audio from text lines using pyttsx3 and saves as a .mp3 file"""
    engine = pyttsx3.init()
    audio_filename = "read_pdf.mp3"
    audio_path = os.path.join(current_app.config["UPLOAD_FOLDER"], audio_filename)

    # Save the text as an audio file
    engine.save_to_file("\n".join(lines), audio_path)
    engine.runAndWait()

    return audio_filename


@main.route("/uploads/<filename>")
def uploaded_file(filename):
    """Serve the audio file after it is generated"""
    return send_from_directory(current_app.config["UPLOAD_FOLDER"], filename)


@main.route("/pdf_metadata", methods=["GET", "POST"])
def pdf_metadata():
    if request.method == "POST":
        pdf_file = request.files.get("file")

        if pdf_file and allowed_file(pdf_file.filename):
            filename = secure_filename(pdf_file.filename)
            upload_folder = current_app.config["UPLOAD_FOLDER"]
            pdf_path = os.path.join(upload_folder, filename)
            pdf_file.save(pdf_path)

            # Extract metadata from the PDF
            metadata = extract_pdf_metadata(pdf_path)

            # Return metadata as JSON for frontend to use in popup
            return jsonify(metadata)

    return render_template("pdf_metadata.html")


def extract_pdf_metadata(pdf_path):
    """Extract metadata like file name, size, number of pages, words, and images from PDF."""
    with pdfplumber.open(pdf_path) as pdf:
        text = ""
        image_count = 0

        for page in pdf.pages:
            text += page.extract_text() or ""
            image_count += len(page.images)

        words = len(text.split())
        pdf_size = os.path.getsize(pdf_path)
        metadata = {
            "name": os.path.basename(pdf_path),
            "size": pdf_size,
            "pages": len(pdf.pages),
            "words": words,
            "images": image_count,
        }

    return metadata
