# ReconAgent 📊

> **Hệ Thống Đối Soát Tài Chính Đa Đối Tác Tự Động**
> GreenNode Claw-a-thon 2026 — Track: Coding & Automation / Data Analysis

## Bài Toán Giải Quyết

Nhóm Operations tại Zalopay mất **1–1,5 giờ/ngày** để đối soát thủ công dữ liệu giao dịch với các đối tác thanh toán quốc tế (Apple, Alipay, Tenpay). Quá trình này gồm:

- Tải file từ SFTP → merge thủ công → VLOOKUP → lọc giao dịch hủy → phân loại lệch ca
- Rủi ro sai sót cao do thao tác Excel thủ công
- Không có cơ chế tự động gộp lũy kế qua ngày nghỉ / ngày trễ file

**ReconAgent** tự động hóa toàn bộ pipeline này, giải phóng **90% khối lượng rà soát thủ công**.

## Tính Năng Chính

| Tính năng | Mô tả |
|-----------|-------|
| 🔍 **Auto-Detect Partner** | Nhận diện đối tác từ tên file, không cần chọn thủ công |
| 📁 **Smart Consolidation** | Gộp nhiều file lẻ (DAILY_SPLIT) thành một batch |
| 🚫 **Cancel Filtering** | Tự động tách cặp giao dịch hủy (Sum=0) ra tab riêng |
| 🔗 **Waterfall Matching** | Khớp 2 lượt: Exact ID → Tolerance sai số phí |
| ⏰ **Dynamic Buffer Window** | Phân loại theo Cut-off + Buffer, không báo lỗi sai |
| 💾 **Pending Pool** | Lưu giao dịch lệch ca để đối soát tự động kỳ sau |
| 📊 **Excel 4 Tab** | Báo cáo đầy đủ: Khớp / Đã Hủy / Pending / Nghi Vấn |
| 🔒 **6 Guardrails** | Bảo vệ dữ liệu gốc, chống race condition, human-in-loop |

## Đối Tác Được Hỗ Trợ

| Đối tác | File Identifier | Cut-off (GMT+7) | File Type |
|---------|----------------|-----------------|-----------|
| Apple | `ZaloPay_AMP` | N/A (file-based) | DAILY_SPLIT |
| Alipay | `A11127580000000` | 23:00:00 | CONSOLIDATED |
| Tenpay | `tgp` | 22:50:00 + 1 min buffer | CONSOLIDATED |

## Cách Sử Dụng

1. Mở giao diện chat tại `http://localhost:8000`
2. Kéo thả file đối tác (CSV/XLSX/TXT) vào drop zone
3. Kéo thả file nội bộ tương ứng
4. Nhấn **Gửi** — agent tự xử lý và xuất Excel trong vài giây

## Kiến Trúc

```
User Browser
    ↓  (drag-drop files + chat)
FastAPI (agent.py)
    ├── ReconciliationEngine (recon_engine.py)  ← pure Python/pandas
    │     ├── Partner Detection
    │     ├── Smart Consolidation
    │     ├── Data Normalization
    │     ├── Cancellation Filter
    │     ├── Waterfall Matching
    │     ├── Buffer Classification
    │     └── Excel Export (openpyxl)
    └── GreenNode LLM API  ← natural language commentary
          (Qwen 3.5 27B / Minimax M.25 / Gemma 4 31B-IT)
```

## Setup & Chạy Local

```bash
# Install dependencies
pip install -r requirements.txt

# Set GreenNode API key (lấy từ GreenNode Portal → IAM → Credentials)
export GREENNODE_API_KEY="your-api-key"
export GREENNODE_API_BASE="https://api.greennode.vn/v1"
export GREENNODE_MODEL="Qwen/Qwen2.5-72B-Instruct"

# Run
python agent.py
# → http://localhost:8000
```

## Deploy lên GreenNode AgentBase

```bash
# Trong folder project (đã clone .agentbase vào đây)
claude

# Claude Code sẽ hỏi config:
# Registry? → Recommended
# Network?  → PUBLIC
# CPU?      → 2CPU/4GB Recommended
```

## Cấu Trúc Folder

```
ReconAgent/
├── CLAUDE.md           ← System prompt (brain của agent)
├── agent.py            ← FastAPI app + chat UI
├── recon_engine.py     ← Core reconciliation logic
├── requirements.txt
├── Dockerfile
├── README.md
└── data/
    ├── pending_pool/   ← Lưu giao dịch chờ (JSON per partner)
    └── output/         ← File Excel kết quả
```

## Biến Môi Trường

| Biến | Mô tả | Default |
|------|-------|---------|
| `GREENNODE_API_KEY` | IAM API Key từ GreenNode Portal | (bắt buộc khi deploy) |
| `GREENNODE_API_BASE` | Base URL của LLM API | `https://api.greennode.vn/v1` |
| `GREENNODE_MODEL` | Model LLM sử dụng | `Qwen/Qwen2.5-72B-Instruct` |
| `PORT` | Port server lắng nghe | `8000` |

## API Endpoints

| Endpoint | Method | Mô tả |
|----------|--------|-------|
| `/` | GET | Chat UI |
| `/api/recon` | POST | Upload files + chạy đối soát |
| `/api/chat` | POST | OpenAI-compatible chat (AgentBase) |
| `/api/download/{filename}` | GET | Tải Excel kết quả |
| `/api/pending/{partner}` | GET | Xem Pending Pool |
| `/api/status` | GET | Health check |

---

*Built for GreenNode Claw-a-thon 2026 — Deadline 17/06/2026 12:00*
