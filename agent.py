"""
agent.py — ReconAgent: Hệ Thống Đối Soát Tài Chính Tự Động
FastAPI application with embedded chat UI.

Deploy on GreenNode AgentBase:
  - POST /api/chat        — main chat endpoint (JSON)
  - POST /api/recon       — file upload + message (multipart)
  - GET  /api/download/{filename} — download Excel output
  - GET  /api/pending/{partner}   — view pending pool
  - GET  /                — chat UI
"""

import os
import json
import asyncio
import uuid
from pathlib import Path
from typing import Optional, List
from datetime import datetime

import httpx
from fastapi import FastAPI, File, UploadFile, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware

from recon_engine import ReconciliationEngine, load_pending_pool, PARTNER_CONFIG, OUTPUT_DIR

# ─────────────────────────────────────────────────────────────────────────────
# Configuration
# ─────────────────────────────────────────────────────────────────────────────

GREENNODE_API_BASE = os.getenv("GREENNODE_API_BASE", "https://api.greennode.vn/v1")
GREENNODE_API_KEY = os.getenv("GREENNODE_API_KEY", "")
GREENNODE_MODEL = os.getenv("GREENNODE_MODEL", "Qwen/Qwen2.5-72B-Instruct")

# Read system prompt from CLAUDE.md
_claude_md_path = Path("CLAUDE.md")
SYSTEM_PROMPT = _claude_md_path.read_text(encoding="utf-8") if _claude_md_path.exists() else (
    "Bạn là ReconAgent, chuyên gia đối soát tài chính tự động cho ZaloPay."
)

# Active reconciliation sessions (RULE 2: prevent race conditions)
_active_sessions: dict = {}

# ─────────────────────────────────────────────────────────────────────────────
# FastAPI App
# ─────────────────────────────────────────────────────────────────────────────

app = FastAPI(
    title="ReconAgent",
    description="Hệ Thống Đối Soát Tài Chính Tự Động — ZaloPay Claw-a-thon 2026",
    version="1.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

engine = ReconciliationEngine()

# ─────────────────────────────────────────────────────────────────────────────
# LLM Client
# ─────────────────────────────────────────────────────────────────────────────

async def call_llm(
    messages: List[dict],
    temperature: float = 0.3,
    max_tokens: int = 1500,
) -> str:
    """Call GreenNode LLM API (OpenAI-compatible)."""
    if not GREENNODE_API_KEY:
        return _local_response(messages)

    payload = {
        "model": GREENNODE_MODEL,
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    headers = {
        "Authorization": f"Bearer {GREENNODE_API_KEY}",
        "Content-Type": "application/json",
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            resp = await client.post(
                f"{GREENNODE_API_BASE}/chat/completions",
                json=payload,
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
            return data["choices"][0]["message"]["content"]
    except httpx.HTTPStatusError as e:
        return f"⚠️ Lỗi kết nối LLM (HTTP {e.response.status_code}). Kết quả đối soát vẫn được xử lý thành công."
    except Exception as e:
        return f"⚠️ Không thể kết nối LLM: {str(e)[:100]}. Kết quả đối soát vẫn được xử lý thành công."


def _local_response(messages: List[dict]) -> str:
    """Fallback response when LLM is unavailable."""
    last_msg = messages[-1]["content"] if messages else ""
    if "pending" in last_msg.lower():
        return "Bạn có thể xem Pending Pool qua endpoint /api/pending/{partner_name}."
    return "ReconAgent sẵn sàng. Hãy upload file đối tác và file nội bộ để bắt đầu đối soát."


def format_recon_summary(result: dict) -> str:
    """Format reconciliation result into Vietnamese chat message."""
    if not result["success"]:
        errors = "\n".join(f"❌ {e}" for e in result["errors"])
        warnings = "\n".join(f"⚠️ {w}" for w in result["warnings"])
        return f"**Không thể thực hiện đối soát:**\n\n{errors}\n{warnings}".strip()

    partner = result["partner"]
    s = result["summary"]

    lines = [
        f"📊 **KẾT QUẢ ĐỐI SOÁT — Đối tác: {partner}**",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📅 **Kỳ đối soát:** {s.get('period', 'N/A')}",
        f"🕐 **Thời gian xuất:** {s.get('report_time', '')}",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📊 **TỔNG QUAN DỮ LIỆU**",
        f"   Tổng dòng Nội bộ  : **{s.get('total_internal', 0):,}** dòng",
        f"   Tổng dòng Đối tác : **{s.get('total_external', 0):,}** dòng",
        f"   Pending Pool (trước): {s.get('pending_pool_before', 0):,} dòng từ kỳ trước",
        "",
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        "📈 **KẾT QUẢ XỬ LÝ**",
        f"✅ Khớp hoàn toàn       : **{s.get('matched', 0):,}** dòng",
        f"🚫 Tự động loại trừ    : **{s.get('cancelled', 0):,}** dòng "
        f"(Cancel/Refund cặp đối ứng triệt tiêu)",
        f"⏳ Treo Pending Pool    : **{s.get('pending', 0):,}** dòng "
        f"(lệch ca chốt sổ → kỳ sau)",
    ]

    disc = s.get("discrepancy", 0)
    if disc > 0:
        lines.append(
            f"🔴 **CẦN XỬ LÝ GẤP       : {disc:,} dòng** "
            f"(lệch tiền thực tế — xem Tab_Lệch_Nghi_Vấn)"
        )
    else:
        lines.append(f"✅ Lệch Nghi Vấn          : **0** dòng — Không có lệch thực tế!")

    lines += [
        "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━",
        f"📁 **File kết quả:** `{result.get('excel_filename', '')}`",
        f"   Pending Pool cập nhật: {s.get('pending_pool_after', 0):,} dòng",
    ]

    if result.get("warnings"):
        lines.append("\n⚠️ **Cảnh báo:**")
        for w in result["warnings"]:
            lines.append(f"  {w}")

    return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
# API Endpoints
# ─────────────────────────────────────────────────────────────────────────────

@app.post("/api/recon")
async def reconcile(
    message: str = Form(default=""),
    partner: Optional[str] = Form(default=None),
    files: List[UploadFile] = File(default=[]),
):
    """
    Main reconciliation endpoint.
    Accepts multipart/form-data with files + optional message.
    """
    if not files:
        return JSONResponse({
            "type": "text",
            "content": (
                "👋 Xin chào! Tôi là **ReconAgent** — hệ thống đối soát tài chính tự động.\n\n"
                "Để bắt đầu đối soát, hãy:\n"
                "1. Kéo thả **file đối tác** (Apple/Alipay/Tenpay) vào khung chat\n"
                "2. Kéo thả **file nội bộ** tương ứng\n"
                "3. Nhấn **Gửi**\n\n"
                "Tôi sẽ tự động nhận diện đối tác, xử lý và xuất báo cáo Excel."
            )
        })

    # RULE 2: Check for active session for same partner
    if partner and partner in _active_sessions:
        return JSONResponse({
            "type": "error",
            "content": (
                f"⚠️ **RULE 2 — Race Condition Prevention**\n\n"
                f"Đang có một phiên đối soát {partner} đang chạy (Session ID: "
                f"{_active_sessions[partner]}). "
                "Vui lòng chờ phiên đó hoàn tất trước khi khởi động phiên mới."
            )
        })

    # Read files
    file_data = []
    for upload in files:
        content = await upload.read()
        file_data.append((upload.filename, content))

    # Detect partner early for race condition check
    if not partner:
        filenames = [f[0] for f in file_data]
        detected = engine.detect_partner(filenames)
        if detected:
            partner = detected

    # Register session
    session_id = str(uuid.uuid4())[:8]
    if partner:
        _active_sessions[partner] = session_id

    try:
        # Run reconciliation
        result = engine.process(file_data, partner=partner)

        # Format response
        summary_text = format_recon_summary(result)

        # Generate LLM commentary if there are discrepancies
        llm_note = ""
        if result["success"] and result["summary"].get("discrepancy", 0) > 0:
            prompt_msgs = [
                {
                    "role": "user",
                    "content": (
                        f"Đối soát {result['partner']} vừa xong với {result['summary']['discrepancy']} "
                        "dòng lệch nghi vấn. Đưa ra 2-3 gợi ý hành động ngắn gọn cho người vận hành."
                    )
                }
            ]
            llm_note = await call_llm(prompt_msgs, max_tokens=300)

        response_content = summary_text
        if llm_note:
            response_content += f"\n\n---\n💡 **Gợi ý xử lý:**\n{llm_note}"

        response = {
            "type": "recon_result",
            "content": response_content,
            "success": result["success"],
            "partner": result.get("partner"),
            "summary": result.get("summary", {}),
        }

        if result.get("excel_filename"):
            response["download_url"] = f"/api/download/{result['excel_filename']}"
            response["excel_filename"] = result["excel_filename"]

        return JSONResponse(response)

    except Exception as e:
        return JSONResponse({
            "type": "error",
            "content": f"❌ Lỗi hệ thống khi xử lý: {str(e)}"
        }, status_code=500)
    finally:
        # Always release the session lock
        if partner and _active_sessions.get(partner) == session_id:
            del _active_sessions[partner]


@app.post("/api/chat")
async def chat(request: Request):
    """
    OpenAI-compatible chat endpoint for AgentBase integration.
    POST { "messages": [...], "stream": false }
    """
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    messages = body.get("messages", [])
    if not messages:
        return JSONResponse({
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": "Xin chào! Tôi là ReconAgent. Hãy upload file đối soát để bắt đầu."
                }
            }]
        })

    response_text = await call_llm(messages)
    return JSONResponse({
        "choices": [{
            "message": {
                "role": "assistant",
                "content": response_text,
            }
        }]
    })


@app.get("/api/download/{filename}")
async def download_excel(filename: str):
    """Download a generated Excel file."""
    # Security: only allow files in OUTPUT_DIR, no path traversal
    safe_filename = Path(filename).name
    filepath = OUTPUT_DIR / safe_filename
    if not filepath.exists():
        raise HTTPException(status_code=404, detail="File không tồn tại.")
    return FileResponse(
        path=str(filepath),
        filename=safe_filename,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.get("/api/pending/{partner}")
async def get_pending(partner: str):
    """View pending pool for a partner."""
    if partner not in PARTNER_CONFIG:
        raise HTTPException(
            status_code=404,
            detail=f"Đối tác '{partner}' không tồn tại. Có: {list(PARTNER_CONFIG.keys())}"
        )
    pool = load_pending_pool(partner)
    if pool.empty:
        return JSONResponse({"partner": partner, "count": 0, "records": []})
    return JSONResponse({
        "partner": partner,
        "count": len(pool),
        "records": pool.to_dict(orient="records"),
    })


@app.get("/health")
async def health():
    """AgentBase Runtime health check — must return HTTP 200."""
    return JSONResponse({"status": "ok"})


@app.get("/api/status")
async def status():
    """Detailed status endpoint."""
    return JSONResponse({
        "status": "ok",
        "agent": "ReconAgent",
        "version": "1.0.0",
        "model": GREENNODE_MODEL,
        "active_sessions": list(_active_sessions.keys()),
        "partners": list(PARTNER_CONFIG.keys()),
    })


# ─────────────────────────────────────────────────────────────────────────────
# Chat UI (served at root)
# ─────────────────────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def index():
    return HTML_TEMPLATE


HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="vi">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>ReconAgent — Đối Soát Tự Động</title>
<style>
  :root {
    --primary: #1F4E79;
    --primary-light: #2E75B6;
    --bg: #F0F4F8;
    --chat-bg: #FFFFFF;
    --user-bubble: #2E75B6;
    --agent-bubble: #FFFFFF;
    --border: #D0DCE8;
    --success: #00B050;
    --danger: #C00000;
    --warn: #FFC000;
    --text: #1A1A2E;
  }
  * { box-sizing: border-box; margin: 0; padding: 0; }
  body { font-family: 'Segoe UI', Arial, sans-serif; background: var(--bg); color: var(--text); height: 100vh; display: flex; flex-direction: column; }

  /* Header */
  header { background: var(--primary); color: #fff; padding: 14px 24px; display: flex; align-items: center; gap: 12px; box-shadow: 0 2px 8px rgba(0,0,0,0.2); }
  header .logo { font-size: 28px; }
  header h1 { font-size: 18px; font-weight: 700; letter-spacing: 0.5px; }
  header .subtitle { font-size: 12px; opacity: 0.75; }
  header .status-dot { width: 10px; height: 10px; background: #00B050; border-radius: 50%; margin-left: auto; animation: pulse 2s infinite; }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.4} }

  /* Chat area */
  #chat-container { flex: 1; overflow-y: auto; padding: 20px; display: flex; flex-direction: column; gap: 16px; }
  .bubble-wrap { display: flex; gap: 10px; max-width: 82%; }
  .bubble-wrap.user { align-self: flex-end; flex-direction: row-reverse; }
  .bubble-wrap.agent { align-self: flex-start; }
  .avatar { width: 36px; height: 36px; border-radius: 50%; display: flex; align-items: center; justify-content: center; font-size: 18px; flex-shrink: 0; }
  .avatar.agent { background: var(--primary); }
  .avatar.user { background: var(--user-bubble); }
  .bubble { padding: 12px 16px; border-radius: 16px; line-height: 1.6; font-size: 14px; white-space: pre-wrap; word-break: break-word; }
  .bubble.agent { background: var(--agent-bubble); border: 1px solid var(--border); border-top-left-radius: 4px; box-shadow: 0 1px 4px rgba(0,0,0,0.07); }
  .bubble.user { background: var(--user-bubble); color: #fff; border-top-right-radius: 4px; }
  .bubble strong { font-weight: 700; }
  .bubble code { background: #f0f4f8; padding: 1px 5px; border-radius: 4px; font-family: monospace; font-size: 13px; }

  /* Download button */
  .download-btn { display: inline-flex; align-items: center; gap: 8px; margin-top: 10px; padding: 8px 16px; background: var(--success); color: #fff; border: none; border-radius: 8px; cursor: pointer; font-size: 13px; font-weight: 600; text-decoration: none; }
  .download-btn:hover { background: #008040; }

  /* Drop zone */
  #drop-zone { border: 2px dashed var(--border); border-radius: 10px; padding: 10px 14px; margin: 0 20px 4px; font-size: 13px; color: #888; text-align: center; transition: all 0.2s; cursor: pointer; }
  #drop-zone.drag-over { border-color: var(--primary-light); background: #EBF3FB; color: var(--primary); }
  #drop-zone.has-files { border-color: var(--success); background: #F0FFF4; color: var(--success); }

  /* File chips */
  #file-list { display: flex; flex-wrap: wrap; gap: 6px; padding: 0 20px; min-height: 0; }
  .file-chip { display: flex; align-items: center; gap: 5px; background: var(--primary); color: #fff; border-radius: 20px; padding: 4px 10px; font-size: 12px; }
  .file-chip .remove { cursor: pointer; opacity: 0.7; font-size: 14px; }
  .file-chip .remove:hover { opacity: 1; }

  /* Input bar */
  #input-bar { display: flex; gap: 10px; padding: 14px 20px; background: var(--chat-bg); border-top: 1px solid var(--border); }
  #message-input { flex: 1; border: 1.5px solid var(--border); border-radius: 24px; padding: 10px 18px; font-size: 14px; outline: none; resize: none; max-height: 120px; font-family: inherit; }
  #message-input:focus { border-color: var(--primary-light); }
  #send-btn { background: var(--primary); color: #fff; border: none; border-radius: 50%; width: 44px; height: 44px; display: flex; align-items: center; justify-content: center; cursor: pointer; font-size: 20px; flex-shrink: 0; transition: background 0.2s; }
  #send-btn:hover { background: var(--primary-light); }
  #send-btn:disabled { background: #ccc; cursor: not-allowed; }

  /* Partner selector */
  #partner-hint { padding: 2px 20px 0; font-size: 12px; color: #888; }
  #partner-hint select { border: 1px solid var(--border); border-radius: 6px; padding: 2px 6px; font-size: 12px; }

  /* Loading indicator */
  .typing { display: flex; gap: 4px; padding: 14px 16px; }
  .typing span { width: 8px; height: 8px; background: var(--primary-light); border-radius: 50%; animation: bounce 1.2s infinite; }
  .typing span:nth-child(2) { animation-delay: 0.2s; }
  .typing span:nth-child(3) { animation-delay: 0.4s; }
  @keyframes bounce { 0%,60%,100%{transform:translateY(0)} 30%{transform:translateY(-8px)} }
</style>
</head>
<body>

<header>
  <span class="logo">📊</span>
  <div>
    <h1>ReconAgent</h1>
    <div class="subtitle">Hệ Thống Đối Soát Tài Chính Tự Động — ZaloPay</div>
  </div>
  <div class="status-dot" title="Online"></div>
</header>

<div id="chat-container">
  <!-- Welcome message -->
  <div class="bubble-wrap agent">
    <div class="avatar agent">📊</div>
    <div class="bubble agent">
<strong>👋 Xin chào! Tôi là ReconAgent.</strong>

Tôi tự động hóa toàn bộ quy trình đối soát tài chính đa đối tác, bao gồm:

✅ Nhận diện đối tác từ tên file (Apple / Alipay / Tenpay)
✅ Lọc giao dịch Cancel/Refund tự động
✅ Khớp phễu 2 lượt (Exact ID → Tolerance)
✅ Phân loại Cut-off & Buffer Window
✅ Quản lý Pending Pool liên kỳ
✅ Xuất báo cáo Excel 4 tab

<strong>Để bắt đầu:</strong> Kéo thả file đối tác và file nội bộ vào ô bên dưới, rồi nhấn Gửi.
    </div>
  </div>
</div>

<div id="drop-zone" onclick="document.getElementById('file-input').click()">
  📎 Kéo thả file vào đây hoặc click để chọn (CSV, XLSX, TXT)
</div>
<input type="file" id="file-input" multiple accept=".csv,.xlsx,.xls,.txt" style="display:none">
<div id="file-list"></div>
<div id="partner-hint">
  Đối tác (auto-detect):
  <select id="partner-select">
    <option value="">-- Tự động nhận diện --</option>
    <option value="Apple">Apple (ZaloPay AMP)</option>
    <option value="Alipay">Alipay</option>
    <option value="Tenpay">Tenpay</option>
  </select>
</div>

<div id="input-bar">
  <textarea id="message-input" rows="1" placeholder="Nhập ghi chú hoặc câu hỏi (không bắt buộc)..." oninput="autoResize(this)"></textarea>
  <button id="send-btn" onclick="sendMessage()">➤</button>
</div>

<script>
const chatContainer = document.getElementById('chat-container');
const fileInput = document.getElementById('file-input');
const dropZone = document.getElementById('drop-zone');
const fileList = document.getElementById('file-list');
let uploadedFiles = [];

// Drag-and-drop
dropZone.addEventListener('dragover', e => { e.preventDefault(); dropZone.classList.add('drag-over'); });
dropZone.addEventListener('dragleave', () => dropZone.classList.remove('drag-over'));
dropZone.addEventListener('drop', e => {
  e.preventDefault();
  dropZone.classList.remove('drag-over');
  addFiles([...e.dataTransfer.files]);
});
fileInput.addEventListener('change', () => addFiles([...fileInput.files]));

function addFiles(files) {
  files.forEach(f => {
    if (!uploadedFiles.find(x => x.name === f.name && x.size === f.size)) {
      uploadedFiles.push(f);
    }
  });
  renderFileChips();
}

function renderFileChips() {
  fileList.innerHTML = uploadedFiles.map((f, i) =>
    `<div class="file-chip">📄 ${f.name} <span class="remove" onclick="removeFile(${i})">✕</span></div>`
  ).join('');
  dropZone.classList.toggle('has-files', uploadedFiles.length > 0);
  if (uploadedFiles.length > 0) {
    dropZone.textContent = `${uploadedFiles.length} file đã chọn — click để thêm`;
  } else {
    dropZone.textContent = '📎 Kéo thả file vào đây hoặc click để chọn (CSV, XLSX, TXT)';
  }
}

function removeFile(index) {
  uploadedFiles.splice(index, 1);
  renderFileChips();
}

function addMessage(role, content, downloadUrl, filename) {
  const wrap = document.createElement('div');
  wrap.className = `bubble-wrap ${role}`;
  const avatar = document.createElement('div');
  avatar.className = `avatar ${role}`;
  avatar.textContent = role === 'user' ? '👤' : '📊';
  const bubble = document.createElement('div');
  bubble.className = `bubble ${role}`;

  // Simple markdown rendering
  let html = escapeHtml(content)
    .replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>')
    .replace(/`([^`]+)`/g, '<code>$1</code>')
    .replace(/━+/g, '<hr style="border:none;border-top:1px solid #ddd;margin:6px 0">');
  bubble.innerHTML = html;

  if (downloadUrl && filename) {
    const btn = document.createElement('a');
    btn.href = downloadUrl;
    btn.className = 'download-btn';
    btn.download = filename;
    btn.innerHTML = '⬇️ Tải file Excel: ' + filename;
    bubble.appendChild(btn);
  }

  wrap.appendChild(avatar);
  wrap.appendChild(bubble);
  chatContainer.appendChild(wrap);
  chatContainer.scrollTop = chatContainer.scrollHeight;
}

function addTypingIndicator() {
  const wrap = document.createElement('div');
  wrap.className = 'bubble-wrap agent';
  wrap.id = 'typing-indicator';
  wrap.innerHTML = '<div class="avatar agent">📊</div><div class="bubble agent"><div class="typing"><span></span><span></span><span></span></div></div>';
  chatContainer.appendChild(wrap);
  chatContainer.scrollTop = chatContainer.scrollHeight;
}

function removeTypingIndicator() {
  const el = document.getElementById('typing-indicator');
  if (el) el.remove();
}

function escapeHtml(text) {
  return text.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;');
}

function autoResize(el) {
  el.style.height = 'auto';
  el.style.height = Math.min(el.scrollHeight, 120) + 'px';
}

async function sendMessage() {
  const msgInput = document.getElementById('message-input');
  const sendBtn = document.getElementById('send-btn');
  const partnerSelect = document.getElementById('partner-select');
  const msg = msgInput.value.trim();

  if (!uploadedFiles.length && !msg) {
    addMessage('agent', '❓ Vui lòng upload file đối soát hoặc nhập câu hỏi trước khi gửi.');
    return;
  }

  // Show user message
  const userMsg = msg || `[Upload ${uploadedFiles.length} file: ${uploadedFiles.map(f=>f.name).join(', ')}]`;
  addMessage('user', userMsg);
  msgInput.value = '';
  msgInput.style.height = 'auto';
  sendBtn.disabled = true;

  addTypingIndicator();

  try {
    const formData = new FormData();
    formData.append('message', msg);
    if (partnerSelect.value) formData.append('partner', partnerSelect.value);
    uploadedFiles.forEach(f => formData.append('files', f));

    const resp = await fetch('/api/recon', { method: 'POST', body: formData });
    const data = await resp.json();

    removeTypingIndicator();
    addMessage('agent', data.content, data.download_url, data.excel_filename);

    // Clear files after successful submission
    if (data.success) {
      uploadedFiles = [];
      renderFileChips();
      partnerSelect.value = '';
    }
  } catch (err) {
    removeTypingIndicator();
    addMessage('agent', '❌ Lỗi kết nối server: ' + err.message);
  } finally {
    sendBtn.disabled = false;
  }
}

// Enter to send (Shift+Enter for newline)
document.getElementById('message-input').addEventListener('keydown', e => {
  if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});
</script>
</body>
</html>
"""


# ─────────────────────────────────────────────────────────────────────────────
# Entry point
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run("agent:app", host="0.0.0.0", port=port, reload=False)
