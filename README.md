# 🖧 Network Lab Exam Platform

A self-hosted, browser-based exam platform for conducting network lab exams.
Students connect to your computer's IP address — no internet required.

---

## ⚡ Quick Setup

### 1. Install Python (if not installed)
Download from https://python.org — make sure to check "Add to PATH"

### 2. Install dependencies
```bash
cd netexam
pip install -r requirements.txt
```

### 3. Run the server
```bash
python app.py
```

### 4. Share your IP with students
Find your local IP:
- **Windows**: Open CMD → type `ipconfig` → look for "IPv4 Address"
- **Linux/Mac**: Open terminal → type `hostname -I`

Tell students to open: `http://YOUR_IP:5000`

---

## 🔑 Default Credentials

| Role    | Username / Reg No | Password  |
|---------|-------------------|-----------|
| Admin   | admin             | admin123  |
| Student | STU001            | alice123  |
| Student | STU002            | bob123    |

**Change admin password**: Edit `ADMIN_PASS` in `app.py`

---

## 📋 Question Categories

| Category        | Description                              |
|----------------|------------------------------------------|
| MCQ / Theory   | Multiple choice theory questions          |
| IP Calculation | Binary conversion, broadcast addresses    |
| DNS            | Record types, port numbers, config        |
| Email Server   | SMTP, IMAP, POP3, ports                  |
| Cable Types    | Straight-through, crossover, RJ-45, etc. |
| Network Topology | Star, mesh, bus, ring topologies        |

---

## 🗂 Data Files

All data is stored as JSON in the `data/` folder:
- `questions.json` — Question bank
- `students.json`  — Student accounts  
- `exams.json`     — Exam configurations
- `results.json`   — All submitted results

---

## 📤 Exporting Results

Go to **Admin → Results → Export CSV** to download all scores as a spreadsheet.

---

## 🔧 Admin Workflow

1. **Add students** → Admin → Students → Add Student
2. **Add questions** → Admin → Questions → Add Question  
3. **Create exam** → Admin → Exams → Create Exam (select questions + duration)
4. **Activate exam** → Click "Activate" to make it live for students
5. **Monitor & export** → Admin → Results
