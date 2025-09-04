import os
import sqlite3
from flask import Flask, render_template, request, redirect, url_for, flash, session, send_from_directory, abort, jsonify, make_response
from werkzeug.utils import secure_filename
import re

# ------------- Config -------------
app = Flask(__name__)
app.secret_key = "super-secret-key-change-me"

BASE_DIR = os.path.abspath(os.path.dirname(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")
UPLOAD_ROOT = os.path.join(STATIC_DIR, "uploads")
os.makedirs(UPLOAD_ROOT, exist_ok=True)

DB_PATH = os.path.join(BASE_DIR, "site.db")
ALLOWED_VIDEO_EXT = {"mp4", "mov", "m4v", "webm", "ogg"}
MAX_CONTENT_LENGTH = 2 * 1024 * 1024 * 1024   # 2GB
app.config["MAX_CONTENT_LENGTH"] = MAX_CONTENT_LENGTH

# ------------- Helpers -------------
def db():
    return sqlite3.connect(DB_PATH, check_same_thread=False)

def init_db():
    con = db()
    cur = con.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        password TEXT
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS courses (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        title TEXT NOT NULL,
        slug TEXT UNIQUE NOT NULL
    )
    """)

    cur.execute("""
    CREATE TABLE IF NOT EXISTS lessons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_id INTEGER NOT NULL,
        title TEXT NOT NULL,
        description TEXT,
        filename TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        likes INTEGER DEFAULT 0,
        views INTEGER DEFAULT 0,
        FOREIGN KEY(course_id) REFERENCES courses(id) ON DELETE CASCADE
    )
    """)

    # default teacher login
    cur.execute("INSERT OR IGNORE INTO users (username, password) VALUES (?, ?)", ("deepika", "teachersday"))
    con.commit()
    con.close()

def slugify(text: str) -> str:
    text = text.strip().lower()
    text = re.sub(r"[^\w\s-]", "", text)
    text = re.sub(r"[\s_-]+", "-", text)
    text = re.sub(r"^-+|-+$", "", text)
    return text or "course"

def allowed_video(filename: str) -> bool:
    return "." in filename and filename.rsplit(".", 1)[1].lower() in ALLOWED_VIDEO_EXT

init_db()

def is_logged_in() -> bool:
    return bool(session.get("user"))

# ------------- Public routes -------------
@app.route("/")
def index():
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, title, slug FROM courses ORDER BY id DESC")
    courses = cur.fetchall()
    con.close()
    return render_template("index.html", courses=courses)

@app.route("/course/<slug>")
def course(slug):
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, title, slug FROM courses WHERE slug=?", (slug,))
    course_row = cur.fetchone()
    if not course_row:
        con.close()
        abort(404)
    course_id, title, slug = course_row
    cur.execute("""SELECT id, title, description, filename, likes, views
                   FROM lessons
                   WHERE course_id=?
                   ORDER BY id ASC""", (course_id,))
    lessons = cur.fetchall()
    con.close()
    return render_template("course.html", course=course_row, lessons=lessons)

@app.route("/lesson/<int:lesson_id>")
def lesson(lesson_id):
    con = db()
    cur = con.cursor()
    cur.execute("""SELECT l.id, l.title, l.description, l.filename,
                          l.likes, l.views,
                          c.title as course_title, c.slug
                   FROM lessons l
                   JOIN courses c ON c.id = l.course_id
                   WHERE l.id=?""", (lesson_id,))
    row = cur.fetchone()
    con.close()
    if not row:
        abort(404)
    return render_template("lesson.html", lesson=row)

# ------------- Auth -------------
@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "").strip()

        con = db()
        cur = con.cursor()
        cur.execute("SELECT id FROM users WHERE username=? AND password=?", (username, password))
        user = cur.fetchone()
        con.close()

        if user:
            session["user"] = username
            flash("Welcome back!", "success")
            return redirect(url_for("dashboard"))
        flash("Invalid credentials", "danger")
    return render_template("login.html")

@app.route("/logout")
def logout():
    session.pop("user", None)
    flash("Logged out.", "info")
    return redirect(url_for("index"))

# ------------- Dashboard (Upload Page) -------------
@app.route("/dashboard")
def dashboard():
    if not is_logged_in():
        return redirect(url_for("login"))
    con = db()
    cur = con.cursor()
    cur.execute("SELECT id, title, slug FROM courses ORDER BY id DESC")
    courses = cur.fetchall()
    con.close()
    return render_template("dashboard.html", courses=courses)

@app.route("/dashboard/<slug>")
def dashboard_course(slug):
    if not is_logged_in():
        return redirect(url_for("login"))
    con = db()
    cur = con.cursor()

    # Fetch the course
    cur.execute("SELECT id, title, slug FROM courses WHERE slug=?", (slug,))
    course = cur.fetchone()
    if not course:
        con.close()
        abort(404)

    # Fetch only that course's lessons
    cur.execute("""SELECT id, title, description, filename, likes, views
                   FROM lessons WHERE course_id=? ORDER BY id DESC""", (course[0],))
    lessons = cur.fetchall()
    con.close()

    return render_template("dashboard_course.html", course=course, lessons=lessons)


@app.route("/add_course", methods=["POST"])
def add_course():
    if not is_logged_in():
        return redirect(url_for("login"))

    title = request.form.get("title", "").strip()
    if not title:
        flash("Course title is required.", "danger")
        return redirect(url_for("dashboard"))

    base_slug = slugify(title)
    slug = base_slug
    con = db()
    cur = con.cursor()

    i = 2
    while True:
        cur.execute("SELECT 1 FROM courses WHERE slug=?", (slug,))
        if not cur.fetchone():
            break
        slug = f"{base_slug}-{i}"
        i += 1

    cur.execute("INSERT INTO courses (title, slug) VALUES (?, ?)", (title, slug))
    con.commit()
    con.close()

    course_dir = os.path.join(UPLOAD_ROOT, slug)
    os.makedirs(course_dir, exist_ok=True)

    flash(f"Course '{title}' created!", "success")
    return redirect(url_for("dashboard"))

@app.route("/add_lesson/<int:course_id>", methods=["POST"])
def add_lesson(course_id):
    if not is_logged_in():
        return redirect(url_for("login"))

    title = request.form.get("title", "").strip()
    description = request.form.get("description", "").strip()
    video = request.files.get("video")

    if not title:
        flash("Lesson title is required.", "danger")
        return redirect(url_for("dashboard"))

    con = db()
    cur = con.cursor()
    cur.execute("SELECT title, slug FROM courses WHERE id=?", (course_id,))
    row = cur.fetchone()
    if not row:
        con.close()
        flash("Course not found.", "danger")
        return redirect(url_for("dashboard"))
    course_title, course_slug = row

    if not video or video.filename == "":
        con.close()
        flash("Please choose a video file.", "danger")
        return redirect(url_for("dashboard"))

    if not allowed_video(video.filename):
        con.close()
        flash("Unsupported video format.", "danger")
        return redirect(url_for("dashboard"))

    course_dir = os.path.join(UPLOAD_ROOT, course_slug)
    os.makedirs(course_dir, exist_ok=True)

    safe_name = secure_filename(video.filename)
    save_path = os.path.join(course_dir, safe_name)
    if os.path.exists(save_path):
        name, ext = os.path.splitext(safe_name)
        idx = 2
        while True:
            try_name = f"{name}-{idx}{ext}"
            try_path = os.path.join(course_dir, try_name)
            if not os.path.exists(try_path):
                safe_name = try_name
                save_path = try_path
                break
            idx += 1

    video.save(save_path)

    relative_path = os.path.join("uploads", course_slug, safe_name).replace("\\", "/")
    cur.execute("""INSERT INTO lessons (course_id, title, description, filename)
                   VALUES (?, ?, ?, ?)""",
                (course_id, title, description, relative_path))
    con.commit()
    con.close()

    flash(f"Lesson '{title}' added to {course_title}!", "success")
    return redirect(url_for("dashboard"))

@app.route("/file/<path:relpath>")
def file(relpath):
    full = os.path.join(STATIC_DIR, relpath)
    if not os.path.isfile(full):
        abort(404)
    directory, filename = os.path.split(full)
    return send_from_directory(directory, filename)

# --------- Like endpoint (cookie-based, one per browser) ---------
@app.route("/like/<int:lesson_id>", methods=["POST"])
def like_lesson(lesson_id):
    con = db()
    cur = con.cursor()
    cookie_name = f"liked_{lesson_id}"

    if request.cookies.get(cookie_name):
        cur.execute("SELECT likes FROM lessons WHERE id = ?", (lesson_id,))
        likes = cur.fetchone()[0]
        con.close()
        return jsonify({"likes": likes, "already": True})

    cur.execute("UPDATE lessons SET likes = likes + 1 WHERE id = ?", (lesson_id,))
    con.commit()
    cur.execute("SELECT likes FROM lessons WHERE id = ?", (lesson_id,))
    likes = cur.fetchone()[0]
    con.close()

    resp = make_response(jsonify({"likes": likes, "already": False}))
    resp.set_cookie(cookie_name, "1", max_age=10*365*24*60*60, path="/")
    return resp

# --------- View endpoint (cookie-based, one per browser) ---------
@app.route("/view/<int:lesson_id>", methods=["POST"])
def view_lesson(lesson_id):
    con = db()
    cur = con.cursor()
    cookie_name = f"viewed_{lesson_id}"

    if request.cookies.get(cookie_name):
        cur.execute("SELECT views FROM lessons WHERE id = ?", (lesson_id,))
        views = cur.fetchone()[0]
        con.close()
        return jsonify({"views": views, "already": True})

    cur.execute("UPDATE lessons SET views = views + 1 WHERE id = ?", (lesson_id,))
    con.commit()
    cur.execute("SELECT views FROM lessons WHERE id = ?", (lesson_id,))
    views = cur.fetchone()[0]
    con.close()

    resp = make_response(jsonify({"views": views, "already": False}))
    resp.set_cookie(cookie_name, "1", max_age=10*365*24*60*60, path="/")
    return resp

# Delete lesson
@app.route("/lesson/delete/<int:lesson_id>", methods=["POST"])
def delete_lesson(lesson_id):
    if not is_logged_in():
        return redirect(url_for("login"))
    con = db()
    cur = con.cursor()
    cur.execute("DELETE FROM lessons WHERE id=?", (lesson_id,))
    con.commit()
    con.close()
    flash("Lesson deleted successfully.", "success")
    return redirect(url_for("dashboard"))

# Edit lesson
@app.route("/lesson/edit/<int:lesson_id>", methods=["GET", "POST"])
def edit_lesson(lesson_id):
    if not is_logged_in():
        return redirect(url_for("login"))
    con = db()
    cur = con.cursor()

    if request.method == "POST":
        title = request.form["title"]
        filename = request.form["filename"]  # or handle new file upload if needed
        cur.execute("UPDATE lessons SET title=?, filename=? WHERE id=?",
                    (title, filename, lesson_id))
        con.commit()
        con.close()
        flash("Lesson updated successfully.", "success")
        return redirect(url_for("dashboard"))

    # GET → load lesson data
    cur.execute("SELECT id, title, filename FROM lessons WHERE id=?", (lesson_id,))
    lesson = cur.fetchone()
    con.close()
    return render_template("edit_lesson.html", lesson=lesson)


# ------------- Run -------------
if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0", port=5000)
