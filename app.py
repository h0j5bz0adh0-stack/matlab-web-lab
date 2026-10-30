import os
import sys
import time
import uuid
import shutil
import sqlite3
import asyncio
import subprocess
from pathlib import Path
from typing import Optional, List
from datetime import datetime

from fastapi import FastAPI, Request, HTTPException, Depends, Cookie, Response
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
PLOTS_DIR = BASE_DIR / "plots_storage"
PLOTS_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = BASE_DIR / "lab.db"

ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "Reza1360")
ADMIN_TOKEN_SECRET = os.getenv("ADMIN_SECRET", "matlab_secret_token_2026")
OCTAVE_BIN = shutil.which("octave-cli") or shutil.which("octave") or "octave-cli"

# --- Database Setup ---
def init_db():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            student_name TEXT,
            student_id TEXT,
            code TEXT,
            stdout TEXT,
            stderr TEXT,
            plots TEXT,
            execution_time REAL,
            status TEXT,
            client_ip TEXT,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.commit()
    conn.close()

init_db()

app = FastAPI(title="MATLAB Web Lab & Teacher Portal")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Concurrency semaphore: prevent more than 2 concurrent octave runs (optimized for 512MB RAM)
EXECUTION_SEMAPHORE = asyncio.Semaphore(2)

# Health endpoint for Render & UptimeRobot pinger
@app.get("/health")
async def health_check():
    return {"status": "ok"}

class RunRequest(BaseModel):
    code: str
    student_name: Optional[str] = "ناشناس"
    student_id: Optional[str] = "---"

class AdminLoginRequest(BaseModel):
    password: str

# --- Helper to verify admin auth ---
def verify_admin(req: Request):
    auth_token = req.cookies.get("admin_token")
    if auth_token != ADMIN_TOKEN_SECRET:
        raise HTTPException(status_code=401, detail="Unauthorized")
    return True

# --- API: Run Octave Code ---
@app.post("/api/run")
async def run_code(req_data: RunRequest, request: Request):
    code = req_data.code.strip()
    if not code:
        return JSONResponse(content={"error": "کدی ارسال نشده است.", "stdout": "", "plots": []}, status_code=400)

    # Sanitize and guard dangerous commands
    blacklisted = ["system(", "system (", "unix(", "dos(", "unlink(", "rmdir(", "delete(", "fork("]
    for b in blacklisted:
        if b in code.lower():
            return JSONResponse(content={
                "error": f"اجرای دستور امنیتی '{b.strip('(')}' در محیط اشتراکی مجاز نیست.",
                "stdout": "",
                "plots": [],
                "execution_time": 0
            }, status_code=403)

    run_id = uuid.uuid4().hex[:12]
    run_tmp_dir = PLOTS_DIR / run_id
    run_tmp_dir.mkdir(parents=True, exist_ok=True)

    # Prepare octave script wrapper to capture plots
    wrapper_code = f"""
try
    graphics_toolkit('gnuplot');
catch
end
set(0, 'defaultfigurevisible', 'off');

% --- USER SCRIPT START ---
{code}
% --- USER SCRIPT END ---

% Auto export any active figures
try
    figs = get(0, 'children');
    for idx = 1:length(figs)
        fname = sprintf('fig_%02d.png', idx);
        print(figs(idx), fname, '-dpng', '-r130');
    end
catch e
    fprintf(stderr, 'Plot Export Error: %s\\n', e.message);
end
"""
    script_path = run_tmp_dir / "main.m"
    script_path.write_text(wrapper_code, encoding="utf-8")

    client_ip = request.client.host if request.client else "unknown"
    start_time = time.time()
    stdout_res = ""
    stderr_res = ""
    status = "success"

    async with EXECUTION_SEMAPHORE:
        try:
            # Run octave in temp dir
            cmd = [
                OCTAVE_BIN,
                "--no-gui",
                "--no-window-system",
                "--quiet",
                "--norc",
                "main.m"
            ]
            env = os.environ.copy()
            env["OMP_NUM_THREADS"] = "1"
            env["OPENBLAS_NUM_THREADS"] = "1"
            env["MKL_NUM_THREADS"] = "1"

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(run_tmp_dir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=env
            )

            try:
                out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=15.0)
                stdout_res = out_b.decode("utf-8", errors="replace")
                raw_err = err_b.decode("utf-8", errors="replace")
                # Filter out benign octave/gnuplot toolkit warnings
                clean_err_lines = []
                skip = False
                for line in raw_err.splitlines():
                    if "warning: using the gnuplot graphics toolkit" in line:
                        skip = True
                    elif skip and line.startswith("The qt toolkit is recommended"):
                        skip = False
                        continue
                    elif not skip:
                        clean_err_lines.append(line)
                stderr_res = "\n".join(clean_err_lines).strip()
                if proc.returncode != 0:
                    status = "error"
            except asyncio.TimeoutError:
                try:
                    proc.kill()
                except Exception:
                    pass
                status = "timeout"
                stderr_res = "خطای مهلت اجرا: مدت‌زمان پردازش کد از حد مجاز (۱۵ ثانیه) بیشتر شد."

        except Exception as e:
            status = "system_error"
            stderr_res = f"خطا در فراخوانی هسته اکتاو: {str(e)}"

    exec_duration = round(time.time() - start_time, 2)

    # Collect generated plots
    generated_plots = []
    for p_file in sorted(run_tmp_dir.glob("fig_*.png")):
        rel_path = f"/plots/{run_id}/{p_file.name}"
        generated_plots.append(rel_path)

    # Save to SQLite
    try:
        conn = sqlite3.connect(DB_PATH)
        cur = conn.cursor()
        cur.execute("""
            INSERT INTO executions (student_name, student_id, code, stdout, stderr, plots, execution_time, status, client_ip)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            req_data.student_name,
            req_data.student_id,
            code,
            stdout_res,
            stderr_res,
            ",".join(generated_plots),
            exec_duration,
            status,
            client_ip
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"DB Error: {e}", file=sys.stderr)

    return {
        "status": status,
        "stdout": stdout_res,
        "stderr": stderr_res,
        "plots": generated_plots,
        "execution_time": exec_duration,
        "run_id": run_id
    }

# --- Plot File Serving ---
@app.get("/plots/{run_id}/{filename}")
async def get_plot(run_id: str, filename: str):
    file_path = PLOTS_DIR / run_id / filename
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Plot not found")
    return FileResponse(file_path, media_type="image/png")

# --- Teacher Admin APIs ---
@app.post("/api/admin/login")
async def admin_login(data: AdminLoginRequest, response: Response):
    if data.password == ADMIN_PASSWORD:
        response.set_cookie(
            key="admin_token",
            value=ADMIN_TOKEN_SECRET,
            httponly=True,
            max_age=3600 * 24 * 7,
            samesite="lax"
        )
        return {"success": True, "message": "ورود موفقیت‌آمیز"}
    raise HTTPException(status_code=401, detail="رمز عبور نادرست است.")

@app.post("/api/admin/logout")
async def admin_logout(response: Response):
    response.delete_cookie("admin_token")
    return {"success": True}

@app.get("/api/admin/stats")
async def admin_stats(_=Depends(verify_admin)):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    
    total_runs = cur.execute("SELECT COUNT(*) as c FROM executions").fetchone()["c"]
    total_students = cur.execute("SELECT COUNT(DISTINCT student_id) as c FROM executions WHERE student_id != '---'").fetchone()["c"]
    recent_runs = cur.execute("SELECT * FROM executions ORDER BY id DESC LIMIT 50").fetchall()
    
    # Students summary
    students = cur.execute("""
        SELECT student_name, student_id, COUNT(*) as run_count, MAX(timestamp) as last_seen, client_ip
        FROM executions
        GROUP BY student_id, student_name
        ORDER BY last_seen DESC
    """).fetchall()
    
    conn.close()
    
    return {
        "total_runs": total_runs,
        "total_students": total_students,
        "recent_runs": [dict(r) for r in recent_runs],
        "students": [dict(s) for s in students]
    }

@app.get("/api/admin/export")
async def admin_export(_=Depends(verify_admin)):
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM executions ORDER BY id ASC").fetchall()
    conn.close()
    return [dict(r) for r in rows]

# --- Static HTML Pages ---
@app.get("/", response_class=HTMLResponse)
async def serve_index():
    index_file = BASE_DIR / "templates" / "index.html"
    return HTMLResponse(content=index_file.read_text(encoding="utf-8"))

@app.get("/admin", response_class=HTMLResponse)
async def serve_admin():
    admin_file = BASE_DIR / "templates" / "admin.html"
    return HTMLResponse(content=admin_file.read_text(encoding="utf-8"))
