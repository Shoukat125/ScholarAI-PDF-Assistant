from flask import Flask, request, jsonify, render_template, session, redirect, url_for, send_from_directory
from dotenv import load_dotenv
from groq import Groq
import httpx
import webbrowser
from database import (
    init_db, get_all_books, get_book_by_id, delete_book,
    save_reading_record, get_reading_history, get_last_read_page,
    get_total_pages_read, add_note, get_notes, delete_note,
    save_summary, get_summaries, save_quiz, get_quizzes,
    set_reading_goal, get_reading_goal, get_dashboard_stats,
    get_admin_password, set_admin_password
)
from pdf_processor import (
    process_and_register_pdf, get_page_content,
    get_pages_content, search_in_pdf, get_total_pages,
    delete_pdf_file, validate_pdf
)
from bot import (
    generate_summary, generate_quiz,
    answer_question, search_and_answer, generate_reading_plan
)
import os
import json
import bcrypt
import logging

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "scholarai_secret_2024")

LOG_FILE = "scholarai.log"

logging.basicConfig(
    filename=LOG_FILE,
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MODEL_NAME = os.getenv("MODEL_NAME", "llama-3.1-8b-instant")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "admin123")

client = Groq(
    api_key=GROQ_API_KEY,
    http_client=httpx.Client()
)

# ==========================================
# INITIALIZE DATABASE
# ==========================================
init_db()

# Setup admin password if not exists
if not get_admin_password():
    hashed = bcrypt.hashpw(ADMIN_PASSWORD.encode(), bcrypt.gensalt()).decode()
    set_admin_password(hashed)

# ==========================================
# PASSWORD UTILS
# ==========================================
def check_password(password, hashed):
    return bcrypt.checkpw(password.encode(), hashed.encode())

def hash_password(password):
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

def login_required():
    return not session.get("admin_logged_in")

# ==========================================
# ROUTES — MAIN
# ==========================================
@app.route("/")
def index():
    if login_required():
        return redirect(url_for("login"))
    stats = get_dashboard_stats()
    books = get_all_books()
    return render_template("index.html", stats=stats, books=books)

# ==========================================
# ROUTES — AUTH
# ==========================================
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        password = request.form.get("password", "")
        hashed = get_admin_password()
        if hashed and check_password(password, hashed):
            session["admin_logged_in"] = True
            return redirect(url_for("index"))
        return render_template("login.html", error="❌ Incorrect password!")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("admin_logged_in", None)
    return redirect(url_for("login"))

# ==========================================
# ROUTES — LIBRARY
# ==========================================
@app.route("/library")
def library():
    if login_required():
        return redirect(url_for("login"))
    books = get_all_books()
    return render_template("library.html", books=books)

@app.route("/upload-book", methods=["POST"])
def upload_book():
    if login_required():
        return redirect(url_for("login"))

    file = request.files.get("pdf")
    title = request.form.get("title", "").strip()
    subject = request.form.get("subject", "").strip()

    if not file or not title:
        books = get_all_books()
        return render_template("library.html", books=books,
                               error="❌ Title aur PDF dono zaroori hain!")

    # Validate PDF
    is_valid, error_msg = validate_pdf(file)
    if not is_valid:
        books = get_all_books()
        return render_template("library.html", books=books, error=f"❌ {error_msg}")

    filename = file.filename.replace(" ", "_")
    book_id = process_and_register_pdf(file, filename, title, subject)

    if book_id:
        books = get_all_books()
        return render_template("library.html", books=books,
                               success=f"✅ '{title}' successfully upload ho gayi!")
    else:
        books = get_all_books()
        return render_template("library.html", books=books,
                               error="❌ PDF upload failed. Please try again.")

@app.route("/delete-book/<int:book_id>", methods=["POST"])
def delete_book_route(book_id):
    if login_required():
        return redirect(url_for("login"))

    book = get_book_by_id(book_id)
    if book:
        delete_pdf_file(book[2])  # filename
        delete_book(book_id)

    return redirect(url_for("library"))

# ==========================================
# ROUTES — READER
# ==========================================
@app.route("/reader/<int:book_id>")
def reader(book_id):
    if login_required():
        return redirect(url_for("login"))

    book = get_book_by_id(book_id)
    if not book:
        return redirect(url_for("library"))

    last_page = get_last_read_page(book_id)
    total_pages = get_total_pages(book_id)
    notes = get_notes(book_id)

    return render_template("reader.html",
                           book=book,
                           last_page=last_page,
                           total_pages=total_pages,
                           notes=notes)

@app.route("/api/get-page", methods=["POST"])
def api_get_page():
    if login_required():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    book_id = data.get("book_id")
    page_number = data.get("page_number", 1)

    content = get_page_content(book_id, page_number)
    if content is None:
        return jsonify({"error": "Page not found"}), 404

    # Save reading record
    save_reading_record(book_id, page_number)

    return jsonify({
        "page_number": page_number,
        "content": content
    })

# ==========================================
# ROUTES — SUMMARY
# ==========================================
@app.route("/summary/<int:book_id>")
def summary_page(book_id):
    if login_required():
        return redirect(url_for("login"))

    book = get_book_by_id(book_id)
    if not book:
        return redirect(url_for("library"))

    total_pages = get_total_pages(book_id)
    summaries = get_summaries(book_id)

    return render_template("summary.html",
                           book=book,
                           total_pages=total_pages,
                           summaries=summaries)

@app.route("/api/generate-summary", methods=["POST"])
def api_generate_summary():
    if login_required():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    book_id = data.get("book_id")
    page_from = int(data.get("page_from", 1))
    page_to = int(data.get("page_to", 1))

    if page_from > page_to:
        return jsonify({"error": "Page from must be less than page to"}), 400

    summary = generate_summary(book_id, page_from, page_to, client, MODEL_NAME)

    # Save to database
    save_summary(book_id, page_from, page_to, summary)

    return jsonify({"summary": summary})

# ==========================================
# ROUTES — QUIZ
# ==========================================
@app.route("/quiz/<int:book_id>")
def quiz_page(book_id):
    if login_required():
        return redirect(url_for("login"))

    book = get_book_by_id(book_id)
    if not book:
        return redirect(url_for("library"))

    total_pages = get_total_pages(book_id)
    return render_template("quiz.html", book=book, total_pages=total_pages)

@app.route("/api/generate-quiz", methods=["POST"])
def api_generate_quiz():
    if login_required():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    book_id = data.get("book_id")
    page_from = int(data.get("page_from", 1))
    page_to = int(data.get("page_to", 1))
    num_questions = int(data.get("num_questions", 5))

    # Limit questions
    num_questions = min(max(num_questions, 3), 10)

    quiz_json = generate_quiz(book_id, page_from, page_to, num_questions, client, MODEL_NAME)

    if not quiz_json:
        return jsonify({"error": "Quiz generation failed"}), 500

    # Save to database
    save_quiz(book_id, page_from, page_to, quiz_json)

    return jsonify({"quiz": json.loads(quiz_json)})

# ==========================================
# ROUTES — NOTES
# ==========================================
@app.route("/notes/<int:book_id>")
def notes_page(book_id):
    if login_required():
        return redirect(url_for("login"))

    book = get_book_by_id(book_id)
    if not book:
        return redirect(url_for("library"))

    notes = get_notes(book_id)
    total_pages = get_total_pages(book_id)

    return render_template("notes.html",
                           book=book,
                           notes=notes,
                           total_pages=total_pages)

@app.route("/api/add-note", methods=["POST"])
def api_add_note():
    if login_required():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    book_id = data.get("book_id")
    page_number = data.get("page_number")
    note_text = data.get("note_text", "").strip()

    if not note_text:
        return jsonify({"error": "Note text is empty"}), 400

    add_note(book_id, page_number, note_text)
    return jsonify({"status": "ok", "message": "✅ Note saved!"})

@app.route("/api/delete-note/<int:note_id>", methods=["POST"])
def api_delete_note(note_id):
    if login_required():
        return jsonify({"error": "Unauthorized"}), 401

    delete_note(note_id)
    return jsonify({"status": "ok"})

# ==========================================
# ROUTES — SEARCH
# ==========================================
@app.route("/search/<int:book_id>")
def search_page(book_id):
    if login_required():
        return redirect(url_for("login"))

    book = get_book_by_id(book_id)
    if not book:
        return redirect(url_for("library"))

    return render_template("search.html", book=book)

@app.route("/api/search", methods=["POST"])
def api_search():
    if login_required():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    if not data:
        return jsonify({"error": "Invalid request"}), 400

    book_id = data.get("book_id")
    query = data.get("query", "").strip()

    if not query:
        return jsonify({"error": "Search query is empty"}), 400

    if len(query) < 2:
        return jsonify({"error": "Query too short — minimum 2 characters"}), 400

    book = get_book_by_id(book_id)
    if not book:
        return jsonify({"error": "Book not found"}), 404

    try:
        results, answer = search_and_answer(book_id, query, client, MODEL_NAME)
        return jsonify({
            "results": results,
            "answer": answer,
            "total": len(results),
            "query": query
        })
    except Exception as e:
        logging.error(f"Search route error: {e}")
        return jsonify({"error": "Search failed. Please try again."}), 500

# ==========================================
# ROUTES — PROGRESS
# ==========================================
@app.route("/progress/<int:book_id>")
def progress_page(book_id):
    if login_required():
        return redirect(url_for("login"))

    book = get_book_by_id(book_id)
    if not book:
        return redirect(url_for("library"))

    total_pages = get_total_pages(book_id)
    pages_read = get_total_pages_read(book_id)
    history = get_reading_history(book_id)
    goal = get_reading_goal(book_id)

    progress_percent = round((pages_read / total_pages * 100), 1) if total_pages > 0 else 0

    return render_template("progress.html",
                           book=book,
                           total_pages=total_pages,
                           pages_read=pages_read,
                           progress_percent=progress_percent,
                           history=history,
                           goal=goal)

@app.route("/api/set-goal", methods=["POST"])
def api_set_goal():
    if login_required():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    book_id = data.get("book_id")
    target_days = int(data.get("target_days", 30))
    total_pages = get_total_pages(book_id)

    plan = generate_reading_plan(total_pages, target_days)
    if not plan:
        return jsonify({"error": "Could not generate plan"}), 500

    set_reading_goal(book_id, target_days, plan["daily_pages"])

    return jsonify({"status": "ok", "plan": plan})

# ==========================================
# ROUTES — Q&A
# ==========================================
@app.route("/api/ask", methods=["POST"])
def api_ask():
    if login_required():
        return jsonify({"error": "Unauthorized"}), 401

    data = request.get_json()
    book_id = data.get("book_id")
    page_from = int(data.get("page_from", 1))
    page_to = int(data.get("page_to", 1))
    question = data.get("question", "").strip()

    if not question:
        return jsonify({"error": "Question is empty"}), 400

    answer = answer_question(book_id, page_from, page_to, question, client, MODEL_NAME)

    return jsonify({"answer": answer})

# ==========================================
# ROUTES — CHANGE PASSWORD
# ==========================================
@app.route("/change-password", methods=["POST"])
def change_password():
    if login_required():
        return redirect(url_for("login"))

    old_password = request.form.get("old_password", "")
    new_password = request.form.get("new_password", "")
    confirm_password = request.form.get("confirm_password", "")

    hashed = get_admin_password()
    stats = get_dashboard_stats()
    books = get_all_books()

    if not check_password(old_password, hashed):
        return render_template("index.html", stats=stats, books=books,
                               pw_error="❌ Old password is incorrect!")
    if new_password != confirm_password:
        return render_template("index.html", stats=stats, books=books,
                               pw_error="❌ Passwords do not match!")
    if len(new_password) < 6:
        return render_template("index.html", stats=stats, books=books,
                               pw_error="❌ Minimum 6 characters required!")

    set_admin_password(hash_password(new_password))
    return render_template("index.html", stats=stats, books=books,
                           pw_success="✅ Password updated successfully!")

# ==========================================
# SERVE UPLOADED PDFs
# ==========================================
@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory("uploads", filename)

# ==========================================
# RUN APP
# ==========================================
if __name__ == "__main__":
    import os
    if os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        webbrowser.open("http://127.0.0.1:5001")
    app.run(debug=True, port=5001)