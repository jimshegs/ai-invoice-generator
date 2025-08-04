# AI Invoice Generator

Flask app that parses free-form text into invoices (OpenAI), lets you review/edit line items, and generates a pixel-perfect PDF (Playwright/Chromium). Supports logo upload, auto/custom invoice numbers, and Amount Paid / Balance Due.

## Features
- Free-form or guided input → structured invoice
- Editable review panel (client/sender, dates, terms, currency, line items)
- Logo upload (stored under `static/uploads/`)
- PDF via headless Chromium (Playwright)
- Print-friendly CSS (prints only the invoice)
- Amount Paid & Balance Due
- Logging with rotating files + global error handler

## Requirements
- Python 3.9+ (3.11 works fine)
- Chromium for Playwright (installed via a one-liner below)

## Quick start

```bash
# 1) Clone and cd
git clone https://github.com/<you>/ai-invoice-generator.git
cd ai-invoice-generator

# 2) Create & activate a virtualenv
python -m venv venv
# macOS/Linux:
source venv/bin/activate
# Windows PowerShell:
# .\venv\Scripts\Activate.ps1

# 3) Install Python deps
pip install -r requirements.txt

# 4) Install the browser for Playwright
python -m playwright install chromium

# 5) Set environment variables
cp .env.example .env
# edit .env and set OPENAI_API_KEY

# 6) Run
python app.py
# visit http://127.0.0.1:5000
