from flask import Flask, render_template, request, jsonify, session, redirect, url_for, send_file
import sqlite3, os, uuid, csv, io, json, random
from datetime import datetime
from functools import wraps
from questions_seed import SEED_QUESTIONS   # ← separated question bank

app = Flask(__name__)
app.secret_key = "netlab_exam_secret_2026"

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "netlab.db")
os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.commit()   # ensure PRAGMAs are applied before any DML
    return conn

def init_db():
    # NOTE: executescript() issues an implicit COMMIT and also resets PRAGMA foreign_keys=OFF,
    # so we use individual execute() calls here to keep FK enforcement active.
    with get_db() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS admins (
            username    TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            password    TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS students (
            reg_no      TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            password    TEXT NOT NULL,
            created_at  TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS questions (
            id           TEXT PRIMARY KEY,
            category     TEXT NOT NULL,
            question     TEXT NOT NULL,
            options      TEXT,
            answer       TEXT NOT NULL,
            points       INTEGER NOT NULL DEFAULT 2,
            time_seconds INTEGER NOT NULL DEFAULT 60,
            created_at   TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS exams (
            id                  TEXT PRIMARY KEY,
            name                TEXT NOT NULL,
            duration_minutes    INTEGER NOT NULL DEFAULT 60,
            active              INTEGER NOT NULL DEFAULT 0,
            created_at          TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS exam_questions (
            exam_id     TEXT NOT NULL REFERENCES exams(id) ON DELETE CASCADE,
            question_id TEXT NOT NULL REFERENCES questions(id) ON DELETE CASCADE,
            position    INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (exam_id, question_id)
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS results (
            id              TEXT PRIMARY KEY,
            student_id      TEXT NOT NULL REFERENCES students(reg_no),
            exam_id         TEXT NOT NULL REFERENCES exams(id),
            earned_points   INTEGER NOT NULL DEFAULT 0,
            total_points    INTEGER NOT NULL DEFAULT 0,
            percentage      REAL NOT NULL DEFAULT 0,
            submitted_at    TEXT NOT NULL
        )""")
        db.execute("""CREATE TABLE IF NOT EXISTS result_answers (
            id              TEXT PRIMARY KEY,
            result_id       TEXT NOT NULL REFERENCES results(id) ON DELETE CASCADE,
            question_id     TEXT NOT NULL,
            question_text   TEXT NOT NULL,
            category        TEXT NOT NULL,
            student_answer  TEXT,
            correct_answer  TEXT NOT NULL,
            is_correct      INTEGER NOT NULL DEFAULT 0,
            points          INTEGER NOT NULL DEFAULT 0,
            earned          INTEGER NOT NULL DEFAULT 0
        )""")

def migrate_db():
    """Add any missing columns to existing databases (safe to run every startup)."""
    with get_db() as db:
        cols = [r[1] for r in db.execute("PRAGMA table_info(questions)").fetchall()]
        if "time_seconds" not in cols:
            db.execute("ALTER TABLE questions ADD COLUMN time_seconds INTEGER NOT NULL DEFAULT 60")

def seed_defaults():
    with get_db() as db:
        # Default admin
        if not db.execute("SELECT 1 FROM admins").fetchone():
            db.execute("INSERT INTO admins VALUES (?,?,?,?)",
                ("admin", "Administrator", "admin123", now()))

        # Default students
        if not db.execute("SELECT 1 FROM students").fetchone():
            for reg, name, pwd in [("STU001", "Alice Johnson", "alice123"),
                                   ("STU002", "Bob Smith", "bob123")]:
                db.execute("INSERT INTO students VALUES (?,?,?,?)", (reg, name, pwd, now()))

        # Seed questions from questions_seed.py — only on a brand-new empty database.
        # Using q_count == 0 (not q_count < len(SEED_QUESTIONS)) so that admin
        # deletions are never undone by a server restart.
        q_count = db.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        if q_count == 0:
            # Wipe stale data respecting FK constraints
            db.execute("PRAGMA foreign_keys=OFF")
            db.execute("DELETE FROM result_answers")
            db.execute("DELETE FROM results")
            db.execute("DELETE FROM exam_questions")
            db.execute("DELETE FROM exams")
            db.execute("DELETE FROM questions")
            db.execute("PRAGMA foreign_keys=ON")

            qids = []
            for cat, q, opts, ans, pts, tsec in SEED_QUESTIONS:
                qid = new_id()
                db.execute("INSERT INTO questions VALUES (?,?,?,?,?,?,?,?)",
                    (qid, cat, q,
                     json.dumps(opts) if opts else None,
                     ans, pts, tsec, now()))
                qids.append(qid)

            # Default exam using ALL sample questions
            eid = new_id()
            db.execute("INSERT INTO exams VALUES (?,?,?,?,?)",
                (eid, "Network Lab Midterm", 60, 1, now()))
            for i, qid in enumerate(qids):
                db.execute("INSERT INTO exam_questions VALUES (?,?,?)", (eid, qid, i))

def now():
    return datetime.now().isoformat()

def new_id():
    return str(uuid.uuid4())

init_db()
migrate_db()
seed_defaults()

# ── Auth decorators ───────────────────────────────────────────────────────────

def admin_required(f):
    @wraps(f)
    def decorated(*a, **kw):
        if not session.get("admin"):
            return redirect(url_for("admin_login"))
        return f(*a, **kw)
    return decorated

def student_required(f):
    @wraps(f)
    def decorated(*a, **kw):
        if not session.get("student_id"):
            return redirect(url_for("student_login"))
        return f(*a, **kw)
    return decorated

# ── Row helpers ───────────────────────────────────────────────────────────────

def row_to_dict(row):
    return dict(row) if row else None

def rows_to_list(rows):
    return [dict(r) for r in rows]

def parse_question(q):
    d = dict(q)
    d["options"] = json.loads(d["options"]) if d.get("options") else None
    return d

# ─── ROOT ─────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("index.html")

# ─── STUDENT AUTH ─────────────────────────────────────────────────────────────

@app.route("/student/login", methods=["GET", "POST"])
def student_login():
    if request.method == "POST":
        data = request.json
        reg  = data.get("reg_no", "").strip().upper()
        pwd  = data.get("password", "").strip()
        with get_db() as db:
            s = db.execute("SELECT * FROM students WHERE reg_no=? AND password=?",
                           (reg, pwd)).fetchone()
        if s:
            session["student_id"]   = s["reg_no"]
            session["student_name"] = s["name"]
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "Invalid registration number or password"})
    return render_template("student_login.html")

@app.route("/student/signup", methods=["POST"])
def student_signup():
    data = request.json
    name = data.get("name", "").strip()
    reg  = data.get("reg_no", "").strip().upper()
    pwd  = data.get("password", "").strip()
    if not name or not reg or not pwd:
        return jsonify({"success": False, "error": "All fields are required"})
    if len(pwd) < 4:
        return jsonify({"success": False, "error": "Password must be at least 4 characters"})
    with get_db() as db:
        if db.execute("SELECT 1 FROM students WHERE reg_no=?", (reg,)).fetchone():
            return jsonify({"success": False, "error": "Registration number already exists"})
        db.execute("INSERT INTO students VALUES (?,?,?,?)", (reg, name, pwd, now()))
    session["student_id"]   = reg
    session["student_name"] = name
    return jsonify({"success": True})

@app.route("/student/logout")
def student_logout():
    session.pop("student_id", None)
    session.pop("student_name", None)
    return redirect(url_for("student_login"))

# ─── STUDENT EXAM ─────────────────────────────────────────────────────────────

@app.route("/student/exam")
@student_required
def student_exam():
    with get_db() as db:
        exam = db.execute("SELECT * FROM exams WHERE active=1 LIMIT 1").fetchone()
        if not exam:
            return render_template("no_exam.html")
        exam = dict(exam)

        already = db.execute(
            "SELECT id FROM results WHERE student_id=? AND exam_id=?",
            (session["student_id"], exam["id"])
        ).fetchone()
        if already:
            return redirect(url_for("student_result", result_id=already["id"]))

        rows = db.execute("""
            SELECT q.* FROM questions q
            JOIN exam_questions eq ON eq.question_id = q.id
            WHERE eq.exam_id = ?
            ORDER BY eq.position
        """, (exam["id"],)).fetchall()

    questions = [parse_question(r) for r in rows]

    # Shuffle questions uniquely per student using deterministic seed
    # (same student always gets same order on refresh, but different from other students)
    seed_str = session["student_id"] + exam["id"]
    rng = random.Random(seed_str)
    rng.shuffle(questions)

    # Also shuffle MCQ options per student so answer positions differ
    for q in questions:
        if q.get("options"):
            opts = q["options"][:]
            rng.shuffle(opts)
            q["options"] = opts
            # answer stays as text value — grading matches by value not by position

    # Strip answer before sending to client
    safe_questions = [{k: v for k, v in q.items() if k != "answer"} for q in questions]
    return render_template("student_exam.html",
        exam=exam, questions=safe_questions,
        student_name=session["student_name"])

@app.route("/student/submit", methods=["POST"])
@student_required
def student_submit():
    data    = request.json
    exam_id = data.get("exam_id")
    answers = data.get("answers", {})

    with get_db() as db:
        exam = db.execute("SELECT * FROM exams WHERE id=?", (exam_id,)).fetchone()
        if not exam:
            return jsonify({"error": "Exam not found"}), 404

        # Guard against duplicate submissions
        existing = db.execute(
            "SELECT id FROM results WHERE student_id=? AND exam_id=?",
            (session["student_id"], exam_id)
        ).fetchone()
        if existing:
            return jsonify({"success": True, "result_id": existing["id"]})

        rows = db.execute("""
            SELECT q.* FROM questions q
            JOIN exam_questions eq ON eq.question_id = q.id
            WHERE eq.exam_id = ?
            ORDER BY eq.position
        """, (exam_id,)).fetchall()
        questions = [parse_question(r) for r in rows]

        total_points  = 0
        earned_points = 0
        result_id = new_id()

        db.execute("INSERT INTO results VALUES (?,?,?,?,?,?,?)",
            (result_id, session["student_id"], exam_id, 0, 0, 0.0, now()))

        for q in questions:
            pts       = q["points"]
            correct   = q["answer"].strip().lower()
            student_a = answers.get(q["id"], "").strip().lower()
            is_correct = (student_a == correct)
            total_points  += pts
            if is_correct:
                earned_points += pts
            db.execute("INSERT INTO result_answers VALUES (?,?,?,?,?,?,?,?,?,?)", (
                new_id(), result_id, q["id"], q["question"], q["category"],
                answers.get(q["id"], ""), q["answer"],
                1 if is_correct else 0, pts, pts if is_correct else 0
            ))

        pct = round(earned_points / total_points * 100, 1) if total_points else 0
        db.execute("UPDATE results SET earned_points=?, total_points=?, percentage=? WHERE id=?",
            (earned_points, total_points, pct, result_id))

    return jsonify({"success": True, "result_id": result_id})

@app.route("/student/result/<result_id>")
@student_required
def student_result(result_id):
    with get_db() as db:
        result = db.execute("""
            SELECT r.*, s.name AS student_name, e.name AS exam_name
            FROM results r
            JOIN students s ON s.reg_no = r.student_id
            JOIN exams e ON e.id = r.exam_id
            WHERE r.id=?
        """, (result_id,)).fetchone()
        if not result or result["student_id"] != session["student_id"]:
            return redirect(url_for("student_exam"))
        breakdown = db.execute(
            "SELECT * FROM result_answers WHERE result_id=?", (result_id,)
        ).fetchall()
    result = dict(result)
    result["breakdown"] = rows_to_list(breakdown)
    return render_template("student_result.html", result=result)

# ─── ADMIN AUTH ───────────────────────────────────────────────────────────────

@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if request.method == "POST":
        data  = request.json
        uname = data.get("username", "").strip()
        pwd   = data.get("password", "").strip()
        with get_db() as db:
            a = db.execute(
                "SELECT * FROM admins WHERE username=? AND password=?",
                (uname, pwd)).fetchone()
        if a:
            session["admin"]      = True
            session["admin_user"] = a["username"]
            session["admin_name"] = a["name"]
            return jsonify({"success": True})
        return jsonify({"success": False, "error": "Invalid username or password"})
    return render_template("admin_login.html")

@app.route("/admin/signup", methods=["POST"])
def admin_signup():
    data  = request.json
    name  = data.get("name", "").strip()
    uname = data.get("username", "").strip().lower()
    pwd   = data.get("password", "").strip()
    if not name or not uname or not pwd:
        return jsonify({"success": False, "error": "All fields are required"})
    if len(pwd) < 4:
        return jsonify({"success": False, "error": "Password must be at least 4 characters"})
    if not uname.replace("_", "").isalnum():
        return jsonify({"success": False, "error": "Username may only contain letters, numbers, underscores"})
    with get_db() as db:
        if db.execute("SELECT 1 FROM admins WHERE username=?", (uname,)).fetchone():
            return jsonify({"success": False, "error": "Username already taken"})
        db.execute("INSERT INTO admins VALUES (?,?,?,?)", (uname, name, pwd, now()))
    session["admin"]      = True
    session["admin_user"] = uname
    session["admin_name"] = name
    return jsonify({"success": True})

@app.route("/admin/logout")
def admin_logout():
    session.pop("admin", None)
    session.pop("admin_user", None)
    session.pop("admin_name", None)
    return redirect(url_for("admin_login"))

# ─── ADMIN DASHBOARD ──────────────────────────────────────────────────────────

@app.route("/admin")
@admin_required
def admin_dashboard():
    with get_db() as db:
        total_students    = db.execute("SELECT COUNT(*) FROM students").fetchone()[0]
        total_questions   = db.execute("SELECT COUNT(*) FROM questions").fetchone()[0]
        total_submissions = db.execute("SELECT COUNT(*) FROM results").fetchone()[0]
        avg_row = db.execute("SELECT AVG(percentage) FROM results").fetchone()[0]
        avg_score = round(avg_row, 1) if avg_row else 0

        exams = rows_to_list(db.execute(
            "SELECT * FROM exams ORDER BY created_at DESC").fetchall())
        # Attach question_ids to each exam for the dashboard card
        for e in exams:
            rows = db.execute(
                "SELECT question_id FROM exam_questions WHERE exam_id=? ORDER BY position",
                (e["id"],)).fetchall()
            e["question_ids"] = [r["question_id"] for r in rows]

        results = rows_to_list(db.execute("""
            SELECT r.*, s.name AS student_name, e.name AS exam_name
            FROM results r
            JOIN students s ON s.reg_no = r.student_id
            JOIN exams e ON e.id = r.exam_id
            ORDER BY r.submitted_at DESC LIMIT 20
        """).fetchall())

    stats = {
        "total_students":    total_students,
        "total_questions":   total_questions,
        "total_submissions": total_submissions,
        "avg_score":         avg_score,
    }
    return render_template("admin_dashboard.html",
                           stats=stats, results=results, exams=exams)

# ── Questions CRUD ────────────────────────────────────────────────────────────

@app.route("/admin/questions")
@admin_required
def admin_questions():
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM questions ORDER BY category, created_at").fetchall()
    questions = [parse_question(r) for r in rows]
    return render_template("admin_questions.html", questions=questions)

@app.route("/admin/questions/add", methods=["POST"])
@admin_required
def add_question():
    data = request.json
    qid  = new_id()
    opts = json.dumps(data["options"]) if data.get("options") else None
    with get_db() as db:
        db.execute("INSERT INTO questions VALUES (?,?,?,?,?,?,?,?)",
            (qid, data["category"], data["question"], opts,
             data["answer"], int(data.get("points", 2)),
             int(data.get("time_seconds", 60)), now()))
    return jsonify({"success": True, "id": qid})

@app.route("/admin/questions/<qid>", methods=["DELETE"])
@admin_required
def delete_question(qid):
    with get_db() as db:
        # Explicitly remove from exam_questions first (belt-and-suspenders
        # alongside ON DELETE CASCADE, in case an older DB has FK disabled).
        db.execute("DELETE FROM exam_questions WHERE question_id=?", (qid,))
        db.execute("DELETE FROM questions WHERE id=?", (qid,))
    return jsonify({"success": True})

@app.route("/admin/questions/<qid>", methods=["PUT"])
@admin_required
def update_question(qid):
    data = request.json
    opts = json.dumps(data["options"]) if data.get("options") else None
    with get_db() as db:
        db.execute("""
            UPDATE questions
            SET category=?, question=?, options=?, answer=?, points=?, time_seconds=?
            WHERE id=?
        """, (data["category"], data["question"], opts, data["answer"],
              int(data.get("points", 2)), int(data.get("time_seconds", 60)), qid))
    return jsonify({"success": True})

# ── Students CRUD ─────────────────────────────────────────────────────────────

@app.route("/admin/students")
@admin_required
def admin_students():
    with get_db() as db:
        students = rows_to_list(
            db.execute("SELECT * FROM students ORDER BY reg_no").fetchall())
    return render_template("admin_students.html", students=students)

@app.route("/admin/students/add", methods=["POST"])
@admin_required
def add_student():
    data = request.json
    reg  = data["reg_no"].strip().upper()
    with get_db() as db:
        if db.execute("SELECT 1 FROM students WHERE reg_no=?", (reg,)).fetchone():
            return jsonify({"success": False, "error": "Registration number already exists"})
        db.execute("INSERT INTO students VALUES (?,?,?,?)",
            (reg, data["name"], data["password"], now()))
    return jsonify({"success": True})

@app.route("/admin/students/<reg_no>", methods=["DELETE"])
@admin_required
def delete_student(reg_no):
    with get_db() as db:
        # Delete result_answers → results → student (FK chain)
        result_ids = [r[0] for r in db.execute(
            "SELECT id FROM results WHERE student_id=?", (reg_no,)).fetchall()]
        for rid in result_ids:
            db.execute("DELETE FROM result_answers WHERE result_id=?", (rid,))
        db.execute("DELETE FROM results WHERE student_id=?", (reg_no,))
        db.execute("DELETE FROM students WHERE reg_no=?", (reg_no,))
    return jsonify({"success": True})

# ── Exams ─────────────────────────────────────────────────────────────────────

@app.route("/admin/exams")
@admin_required
def admin_exams():
    with get_db() as db:
        exams = rows_to_list(
            db.execute("SELECT * FROM exams ORDER BY created_at DESC").fetchall())
        questions = [parse_question(r) for r in
                     db.execute("SELECT * FROM questions ORDER BY category").fetchall()]
        for e in exams:
            rows = db.execute(
                "SELECT question_id FROM exam_questions WHERE exam_id=? ORDER BY position",
                (e["id"],)).fetchall()
            e["question_ids"] = [r["question_id"] for r in rows]
    return render_template("admin_exams.html", exams=exams, questions=questions)

@app.route("/admin/exams/add", methods=["POST"])
@admin_required
def add_exam():
    data = request.json
    eid  = new_id()
    with get_db() as db:
        db.execute("INSERT INTO exams VALUES (?,?,?,?,?)",
            (eid, data["name"], int(data["duration_minutes"]),
             1 if data.get("active") else 0, now()))
        for i, qid in enumerate(data["question_ids"]):
            db.execute("INSERT INTO exam_questions VALUES (?,?,?)", (eid, qid, i))
    return jsonify({"success": True, "id": eid})

@app.route("/admin/exams/<eid>/toggle", methods=["POST"])
@admin_required
def toggle_exam(eid):
    with get_db() as db:
        cur = db.execute("SELECT active FROM exams WHERE id=?", (eid,)).fetchone()
        new_state = 0 if cur["active"] else 1
        if new_state == 1:              # deactivate all others first (only one live at a time)
            db.execute("UPDATE exams SET active=0")
        db.execute("UPDATE exams SET active=? WHERE id=?", (new_state, eid))
    return jsonify({"success": True})

@app.route("/admin/exams/<eid>", methods=["DELETE"])
@admin_required
def delete_exam(eid):
    with get_db() as db:
        db.execute("DELETE FROM exams WHERE id=?", (eid,))
    return jsonify({"success": True})

# ── Results ───────────────────────────────────────────────────────────────────

@app.route("/admin/results")
@admin_required
def admin_results():
    with get_db() as db:
        results = rows_to_list(db.execute("""
            SELECT r.*, s.name AS student_name, e.name AS exam_name
            FROM results r
            JOIN students s ON s.reg_no = r.student_id
            JOIN exams e ON e.id = r.exam_id
            ORDER BY r.submitted_at DESC
        """).fetchall())
    return render_template("admin_results.html", results=results)

@app.route("/admin/results/<result_id>")
@admin_required
def admin_result_detail(result_id):
    with get_db() as db:
        result = db.execute("""
            SELECT r.*, s.name AS student_name, e.name AS exam_name
            FROM results r
            JOIN students s ON s.reg_no = r.student_id
            JOIN exams e ON e.id = r.exam_id
            WHERE r.id=?
        """, (result_id,)).fetchone()
        breakdown = rows_to_list(db.execute(
            "SELECT * FROM result_answers WHERE result_id=?",
            (result_id,)).fetchall())
    result = dict(result)
    result["breakdown"] = breakdown
    return render_template("admin_result_detail.html", result=result)

@app.route("/admin/export/csv")
@admin_required
def export_csv():
    with get_db() as db:
        results = rows_to_list(db.execute("""
            SELECT r.*, s.name AS student_name, e.name AS exam_name
            FROM results r
            JOIN students s ON s.reg_no = r.student_id
            JOIN exams e ON e.id = r.exam_id
            ORDER BY r.submitted_at DESC
        """).fetchall())
    si = io.StringIO()
    cw = csv.writer(si)
    cw.writerow(["Student ID", "Student Name", "Exam", "Submitted At",
                 "Score", "Total", "Percentage"])
    for r in results:
        cw.writerow([r["student_id"], r["student_name"], r["exam_name"],
                     r["submitted_at"], r["earned_points"],
                     r["total_points"], r["percentage"]])
    output = io.BytesIO()
    output.write(si.getvalue().encode())
    output.seek(0)
    return send_file(output, mimetype="text/csv",
                     download_name="exam_results.csv", as_attachment=True)

# ── API helpers ───────────────────────────────────────────────────────────────

@app.route("/api/questions")
@admin_required
def api_questions():
    with get_db() as db:
        rows = db.execute("SELECT * FROM questions ORDER BY category").fetchall()
    return jsonify([parse_question(r) for r in rows])

if __name__ == "__main__":
    print("\n" + "=" * 55)
    print("  Network Lab Exam Platform  (SQLite backend)")
    print("  Admin  : http://YOUR_IP:5000/admin/login")
    print("  Student: http://YOUR_IP:5000/student/login")
    print("  Default admin : admin / admin123")
    print("  Default student: STU001 / alice123")
    print("  DB file: data/netlab.db")
    print("=" * 55 + "\n")
    app.run(host="0.0.0.0", port=5000, debug=False)