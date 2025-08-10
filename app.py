from flask import Flask, url_for, render_template, request, jsonify, make_response, session, redirect
import re
from datetime import datetime
from dateutil import parser
import json
from playwright.sync_api import sync_playwright
from io import BytesIO
import openai
import os, secrets
from dotenv import load_dotenv
from werkzeug.utils import secure_filename
from logging_config import LOGGING
import logging.config
import copy
from functools import lru_cache, wraps
import re as _re
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from werkzeug.security import check_password_hash


# Load environment variables
load_dotenv()
# Configure logging
logging.config.dictConfig(LOGGING)
logger = logging.getLogger(__name__)

app = Flask(__name__)

app.secret_key = os.getenv("SECRET_KEY", "dev-secret")  # fallback is optional but good

# Ensure SECRET_KEY is set for sessions (already in your CI; keep here for safety)
if not os.getenv("SECRET_KEY"):
    # In prod, set this via env. This fallback is only for dev.
    app.config["SECRET_KEY"] = app.config.get("SECRET_KEY") or secrets.token_hex(16)

from pdf_utils import html_to_pdf

from db import init_db, next_invoice_no, persist_invoice
init_db()

# Ensure the static uploads directory exists
UPLOAD_FOLDER = os.path.join(app.static_folder, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Allow only common image extensions for uploaded logos.
ALLOWED_LOGO_EXTENSIONS = {'.png', '.jpg', '.jpeg', '.webp'}

# Cap request size to 2 MB to avoid giant uploads (Flask will 413 if exceeded).
app.config['MAX_CONTENT_LENGTH'] = 2 * 1024 * 1024


# Configure OpenAI
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')
if not OPENAI_API_KEY:
    raise RuntimeError("Set OPENAI_API_KEY in .env")

openai.api_key = OPENAI_API_KEY

# ---- Auth/CSRF toggles: enabled in real runs, disabled in tests ----
_IS_TESTING = (os.getenv("FLASK_ENV") == "testing") or bool(os.getenv("PYTEST_CURRENT_TEST"))
ENFORCE_AUTH = os.getenv("AUTH_ENABLED", "1") != "0" and not _IS_TESTING
ENFORCE_CSRF = os.getenv("CSRF_ENABLED", "1") != "0" and not _IS_TESTING

app.logger.info("Auth enabled=%s | CSRF enabled=%s", ENFORCE_AUTH, ENFORCE_CSRF)


def _current_user():
    return session.get("user")

def login_required(view):
    @wraps(view)
    def _wrap(*args, **kwargs):
        if not ENFORCE_AUTH:
            return view(*args, **kwargs)
        if not _current_user():
            nxt = request.path
            return redirect(url_for("login", next=nxt))
        return view(*args, **kwargs)
    return _wrap

def require_csrf(view):
    @wraps(view)
    def _wrap(*args, **kwargs):
        if not ENFORCE_CSRF:
            return view(*args, **kwargs)
        token = request.headers.get("X-CSRF-Token") or request.form.get("csrf_token") or request.args.get("csrf_token")
        if not token or token != session.get("csrf_token"):
            return jsonify({"error": "CSRF token missing/invalid"}), 400
        return view(*args, **kwargs)
    return _wrap

@app.context_processor
def inject_csrf():
    # Lets templates expose a token for JS to read; empty when disabled.
    return {"CSRF_TOKEN": session.get("csrf_token", "")}


# ---------- LRU cache for parse ----------

SCHEMA_VERSION = "2025-08-08-v1"   # bump this when you change the tool schema/prompt

def _normalize_prompt(s: str) -> str:
    """Trim & collapse whitespace so near-identical inputs share a cache key."""
    return _re.sub(r"\s+", " ", (s or "").strip())

def _to_float(x, default=0.0, *, minval=None, maxval=None):
    try:
        v = float(x)
    except (TypeError, ValueError):
        v = default
    if minval is not None and v < minval:
        v = minval
    if maxval is not None and v > maxval:
        v = maxval
    return v

def _norm_date(s, default_iso=None):
    """
    Accepts many formats; returns YYYY-MM-DD or the provided default.
    Uses the already-imported `from dateutil import parser` module.
    """
    if not s:
        return default_iso
    try:
        return parser.parse(str(s)).date().isoformat()
    except Exception:
        return default_iso

def _normalize_invoice_payload(data: dict) -> dict:
    """
    Normalize + lightly validate the payload shape expected by your templates/routes.
    - Keeps your current field names.
    - Leaves unknown fields alone.
    - Does NOT raise on empty line_items (subtotal just becomes 0.00).
    """
    today = datetime.now().date().isoformat()

    out = {}
    out['client']          = (data.get('client') or 'Client').strip()
    out['client_address']  = (data.get('client_address') or '').strip()
    out['sender']          = (data.get('sender') or 'Your Business').strip()
    out['sender_address']  = (data.get('sender_address') or '').strip()
    out['payment_terms']   = (data.get('payment_terms') or '').strip()

    # Invoice number: preserve whatever you've already decided upstream
    inv_no = (data.get('invoice_no') or '').strip()
    if inv_no:
        out['invoice_no'] = inv_no

    # Dates → ISO; keep due_date blank if unparsable/missing
    out['invoice_date'] = _norm_date(data.get('invoice_date'), default_iso=today)
    out['due_date']     = _norm_date(data.get('due_date'), default_iso='')

    # Currency symbol: keep short & simple (your templates expect symbol, not code)
    cur = (data.get('currency_symbol') or '£')
    out['currency_symbol'] = str(cur)[:4]

    # Tax rate: accept 0–1 or 0–100; clamp to 0..1
    tr = _to_float(data.get('tax_rate'), 0.0, minval=0.0)
    if tr > 1.0 and tr <= 100.0:
        tr = tr / 100.0
    out['tax_rate'] = min(tr, 1.0)

    # Amount paid: no negatives
    out['amount_paid'] = _to_float(data.get('amount_paid'), 0.0, minval=0.0)

    # Line items: description (trim), numbers coerced & clamped ≥ 0
    items_in = data.get('line_items') or []
    items_out = []
    for it in items_in:
        desc = (it.get('description') or '').strip()
        if not desc:
            continue
        qty   = _to_float(it.get('quantity'),   0.0, minval=0.0)
        price = _to_float(it.get('unit_price'), 0.0, minval=0.0)
        items_out.append({'description': desc, 'quantity': qty, 'unit_price': price})
    out['line_items'] = items_out

    return {**data, **out}  # keep any extra keys the UI added, but prefer normalized


def _ai_parse_invoice_uncached(prompt: str, schema_version: str) -> dict:
    """
    Uncached call: delegates to your existing AI parser that uses OpenAI tool-calling
    and falls back to regex. Keep all logic in AIInvoiceParser.parse().
    """
    return parser_instance.parse(prompt)

@lru_cache(maxsize=128)
def ai_parse_invoice_cached(prompt: str, schema_version: str) -> dict:
    """
    Cached wrapper. DO NOT mutate the returned dict elsewhere. In the route, we
    deep-copy before returning to avoid accidental mutation of the cached object.
    """
    return _ai_parse_invoice_uncached(prompt, schema_version)

# --- Rate limiting setup ----------------------------------------
def real_ip():
    """
    Identify the client. For local dev, request.remote_addr is fine.
    If you deploy behind a proxy, consider ProxyFix or honor X-Forwarded-For.
    """
    # If you later add ProxyFix, get_remote_address will pick it up.
    return get_remote_address()

# Choose storage: memory for dev; Redis for prod.
RATELIMIT_STORAGE_URL = os.getenv("RATELIMIT_STORAGE_URL", "memory://")

limiter = Limiter(
    key_func=real_ip,
    storage_uri=RATELIMIT_STORAGE_URL,
    strategy="moving-window",    # smoother than fixed windows
    app=app,                     # attach to your existing global app
    default_limits=[],           # we'll decorate just the routes we want
)


# ---------- AI Invoice Parser with OpenAI function calling ----------
class AIInvoiceParser:
    def __init__(self):
        # Keep the regex parser as fallback
        self.fallback_parser = InvoiceParser()
    
    def parse(self, text):
        """Use OpenAI function calling to parse invoice description with structured output"""
        logger.info(f"🤖 AI Parser called with: '{text}'")
        
        try:
            # Define the function schema for invoice extraction
            invoice_extraction_function = {
                "type": "function",
                "function": {
                    "name": "extract_invoice_data",
                    "description": "Extract structured invoice information from a natural language description",
                    "parameters": {
                        "type": "object",

                        "properties": {
                            "client":          {"type": "string"},
                            "client_address":  {"type": "string"},
                            "sender":          {"type": "string"},
                            "sender_address":  {"type": "string"},
                            "invoice_no":      {"type": "string"},
                            "invoice_date":    {"type": "string", "description": "ISO date"},
                            "due_date":        {"type": "string", "description": "ISO date"},
                            "payment_terms":   {"type": "string"},
                            "currency_symbol": {"type": "string"},
                            "tax_rate":        {"type": "number", "description": "0-1"},
                            "line_items": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "description": {"type": "string"},
                                        "quantity":    {"type": "number"},
                                        "unit_price":  {"type": "number"}
                                    },
                                    "required": ["description", "quantity", "unit_price"]
                                }
                            },
                            "confidence": {
                                "type": "number",
                                "description": "Confidence level from 0-1 indicating how certain the extraction is"
                            }
                        },
                        "required": ["client", "line_items"]        
                    }
                }
            }

            logger.info("🚀 Calling OpenAI API with function calling...")
            response = openai.chat.completions.create(
                model="gpt-3.5-turbo",
                messages=[
                    {
                        "role": "system", 
                        "content": f"You are an expert invoice data extraction assistant. Extract invoice information accurately from natural language. Today's date is {datetime.now().strftime('%B %d, %Y')}."
                    },

                    {
                        "role": "user",
                        "content": f"""
                        Extract invoice data from this description: '{text}'
                        Return JSON with these keys exactly:

                        client, client_address,
                        line_items (array of objects: description, quantity, unit_price),
                        currency_symbol, tax_rate, invoice_date,
                        amount (numeric grand total), description (short project label)

                        If any field is missing, leave it empty.
                        """
                        }
                ],
                tools=[invoice_extraction_function],
                tool_choice={"type": "function", "function": {"name": "extract_invoice_data"}},  # Force function use
                temperature=0
            )
            
            # Get the function call result
            message = response.choices[0].message
            
            if message.tool_calls and len(message.tool_calls) > 0:
                function_call = message.tool_calls[0]

                function_args = json.loads(function_call.function.arguments)

                # 1️⃣  Keep every field GPT returns
                result = {**function_args}

                # 2️⃣  Add legacy fallbacks so old UI doesn't break
                result.setdefault('description', 'Professional services')
                result.setdefault('amount', 0.0)
                result.setdefault('date', datetime.now().strftime('%B %d, %Y'))
                result['confidence'] = float(function_args.get('confidence', 0.5))
                result['raw_text']   = text
                result['ai_processed'] = True


                logger.info(f"✅ OpenAI function call result: {function_args}")

                logger.info(f"🎯 AI successfully parsed with confidence {result['confidence']}: {result}")
                return result
            else:
                raise ValueError("No function call in response")
                
        except Exception as e:
            logger.error(f"❌ OpenAI API error: {e}")
            # Fall back to regex parser
            logger.info("🔄 Falling back to regex parser...")
            fallback_result = self.fallback_parser.parse(text)
            fallback_result['ai_processed'] = False
            fallback_result['confidence'] = 0.3  # Lower confidence for fallback
            return fallback_result

class InvoiceParser:
    def __init__(self):
        self.patterns = {
            'amount': [
                r'\$(\d+(?:,\d{3})*(?:\.\d{2})?)',  # $500, $1,000.50
                r'(\d+(?:,\d{3})*(?:\.\d{2})?)\s*(?:dollars?|usd|\$)',  # 500 dollars
                r'(\d+)\s*(?:hours?|hrs?)\s*(?:at|@)\s*\$?(\d+)',  # 3 hours at $100
            ],
            'client': [
                r'(?:bill|invoice|for)\s+([A-Z][a-zA-Z\s&]+?)(?:\s+for|\s+\$|\s+\d)',
                r'(?:to|client:?)\s+([A-Z][a-zA-Z\s&]+?)(?:\s+for|\s+\$|\s+\d)',
            ],
            'date': [
                r'(?:on|date|completed|done)\s+([A-Za-z]+\s+\d{1,2}(?:st|nd|rd|th)?,?\s*\d{4}?)',
                r'(\d{1,2}[/-]\d{1,2}[/-]\d{2,4})',
                r'([A-Za-z]+\s+\d{1,2})',  # March 15
            ],
            'description': [
                r'for\s+([a-zA-Z\s]+?)(?:\s*,|\s*\$|\s*\d|\s*on|\s*completed)',
            ]
        }
    
    def extract_amount(self, text):
        for pattern in self.patterns['amount']:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                if len(match.groups()) == 2:  # hours * rate pattern
                    hours = float(match.group(1))
                    rate = float(match.group(2))
                    return hours * rate
                else:
                    amount_str = match.group(1).replace(',', '')
                    return float(amount_str)
        return None
    
    def extract_client(self, text):
        for pattern in self.patterns['client']:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        return None
    
    def extract_date(self, text):
        for pattern in self.patterns['date']:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                try:
                    date_str = match.group(1)
                    # Handle partial dates like "March 15" by adding current year
                    if not re.search(r'\d{4}', date_str):
                        date_str += f" {datetime.now().year}"
                    parsed_date = parser.parse(date_str)
                    return parsed_date.strftime("%B %d, %Y")
                except:
                    continue
        return datetime.now().strftime("%B %d, %Y")
    
    def extract_description(self, text):
        for pattern in self.patterns['description']:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(1).strip()
        
        # Fallback: try to extract service-related words
        services = re.findall(r'\b(?:consulting|design|development|web|website|logo|marketing|writing|content|photography|training|support|maintenance|repair|installation|setup)\w*\b', text, re.IGNORECASE)
        if services:
            return ' '.join(services)
        
        return "Professional services"
    
    def parse(self, text):
        return {
            'client': self.extract_client(text) or "Client Name",
            'amount': self.extract_amount(text) or 0,
            'date': self.extract_date(text),
            'description': self.extract_description(text),
            'raw_text': text
        }

parser_instance = AIInvoiceParser()

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/parse', methods=['POST'])
@limiter.limit("2 per second; 10 per minute")      # per-IP burst + steady
@limiter.limit("1000 per day", key_func=lambda: "global")  
def parse_invoice():
    data = request.get_json(force=True) or {}
    raw_text = data.get('text', '')
    prompt = _normalize_prompt(raw_text)

    if not prompt:
        return jsonify({'error': 'Please provide invoice description'}), 400

    try:
        before = ai_parse_invoice_cached.cache_info()  # has .hits, .misses, .currsize
        parsed = ai_parse_invoice_cached(prompt, SCHEMA_VERSION)
        after  = ai_parse_invoice_cached.cache_info()

        if after.hits > before.hits:
            logger.info("🧠 Parse cache HIT (len=%d, cache=%d)", len(prompt), after.currsize)
        else:
            logger.info("🧠 Parse cache MISS (len=%d, cache=%d)", len(prompt), after.currsize)

        # IMPORTANT: deep-copy so downstream code can’t mutate the cached object
        safe = copy.deepcopy(parsed)
        return jsonify(safe), 200

    except Exception:
        logger.exception("Parse failed")
        return jsonify({'error': 'Server error'}), 500

@app.get("/admin/cache/parse/info")
def parse_cache_info():
    info = ai_parse_invoice_cached.cache_info()
    return jsonify({
        "hits": info.hits,
        "misses": info.misses,
        "currsize": info.currsize
    })

@app.post('/admin/cache/parse/clear')
def clear_parse_cache():
    ai_parse_invoice_cached.cache_clear()
    return jsonify({"ok": True, "message": "parse cache cleared"})

@app.route('/generate', methods=['POST'])
@limiter.limit("20 per minute")
def generate_invoice():
    # 1 -- grab payload & optional logo
    if request.content_type.startswith('multipart/form-data'):
        payload = json.loads(request.form['payload'])
        file = request.files.get('logo')
    else:                                         # fallback for JSON callers
        payload = request.get_json(force=True) or {}
        file = None

    user_number = payload.get('invoice_no', '').strip()
    if user_number:
        invoice_no = user_number        # Respect user override
    else:
        invoice_no, placeholder_id = next_invoice_no()
        payload['invoice_no'] = invoice_no

    logo_url = None
    # if file and file.filename:
    #     fname = secure_filename(file.filename)
    #     save_path = os.path.join(app.config['UPLOAD_FOLDER'], fname)
    #     file.save(save_path)
    #     logo_url = url_for('static', filename=f'uploads/{fname}', _external=True)

    if file and file.filename:
        fname = secure_filename(file.filename)
        ext = os.path.splitext(fname)[1].lower()
        if ext not in ALLOWED_LOGO_EXTENSIONS:
            return make_response("Only PNG, JPG, JPEG, or WEBP logo files are allowed.", 400)

        save_path = os.path.join(app.config['UPLOAD_FOLDER'], fname)
        file.save(save_path)
        logo_url = url_for('static', filename=f'uploads/{fname}', _external=True)

    # Normalize payload to ensure types and sane values
    payload = _normalize_invoice_payload(payload)

    # 2 -- compute totals (same as before)
    items = payload.get('line_items', [])
    tax_rate  = float(payload.get('tax_rate', 0.0))
    subtotal  = sum(i['quantity']*i['unit_price'] for i in items)
    tax_amt   = subtotal * tax_rate
    total     = subtotal + tax_amt

    amount_paid = float(payload.get('amount_paid', 0.0))
    balance_due = total - amount_paid


    persist_invoice(invoice_no, payload)

    return render_template(
        'invoice.html',
        **payload,
        logo_url     = logo_url,
        subtotal     = subtotal,
        #invoice_no   = invoice_no,
        # tax_rate     = tax_rate,
        tax_amount   = tax_amt,
        total        = total,
        balance_due   = balance_due
    )


@app.route('/download-pdf', methods=['POST'])
@limiter.limit("30 per minute")
def download_pdf():
    data = request.get_json(force=True) or {}
    data = _normalize_invoice_payload(data)

    # ---------- 1. Header / parties ----------
    invoice_no  = data.get('invoice_no') or f"INV-{datetime.now():%Y%m%d-%H%M}"
    client          = data.get('client', 'Client')
    client_address  = data.get('client_address', '')
    sender          = data.get('sender', 'Your Business')
    sender_address  = data.get('sender_address', '')
    invoice_date    = data.get('invoice_date', datetime.now().date().isoformat())
    due_date        = data.get('due_date', '')
    payment_terms   = data.get('payment_terms', '')
    currency        = data.get('currency_symbol', '£')
    logo_url        = data.get('logo_url', '')
    amount_paid     = float(data.get('amount_paid', 0.0))

    # ---------- 2. Line items & totals ----------
    items = data.get('line_items', [])
    tax_rate   = float(data.get('tax_rate', 0.0))
    subtotal   = sum(i['quantity'] * i['unit_price'] for i in items)
    tax_amount = subtotal * tax_rate
    total      = subtotal + tax_amount
    balance_due = total - amount_paid

    # ---------- 3. Render SAME template as HTML preview ----------
    html = render_template(
        "invoice_pdf2.html",          # or 'invoice_pdf.html' if you prefer
        invoice_no = invoice_no,
        client = client,
        client_address = client_address,
        sender = sender,
        sender_address = sender_address,
        invoice_date = invoice_date,
        due_date = due_date,
        payment_terms = payment_terms,
        currency_symbol = currency,
        line_items = items,
        subtotal = subtotal,
        tax_rate = tax_rate,
        tax_amount = tax_amount,
        total = total,
        logo_url = logo_url,
        amount_paid = amount_paid,
        balance_due = balance_due
    )

    

    # ---------- 4. Convert HTML → PDF with Playwright (PERFECT rendering) ----------
    try:
        pdf_bytes = html_to_pdf(html)
    except Exception:
        app.logger.exception("PDF generation failed for %s", invoice_no)
        return "Error generating PDF", 500

    # Decide filename once
    pdf_dir = os.path.join(app.static_folder, "invoices")
    os.makedirs(pdf_dir, exist_ok=True)
    pdf_file  = f"{invoice_no}.pdf"
    pdf_path  = os.path.join(pdf_dir, pdf_file)

    with open(pdf_path, "wb") as f:
        f.write(pdf_bytes)

    # Store path (relative, for serving later)
    from db import update_pdf_path
    update_pdf_path(invoice_no, f"invoices/{pdf_file}")

    # Create response with PDF bytes
    response = make_response(pdf_bytes)
    response.headers['Content-Type'] = 'application/pdf'
    response.headers['Content-Disposition'] = f'attachment; filename="{invoice_no}.pdf"'
    return response


from db import list_invoices

@app.route("/history")
@login_required
def history():
    q = (request.args.get("q") or "").strip()
    page = max(int(request.args.get("page", 1) or 1), 1)
    per_page = 10

    rows, total = list_invoices(q if q else None, page, per_page)

    invoices = []
    for r in rows:
        payload = json.loads(r["payload_json"] or "{}")
        client  = payload.get("client", "—")
        items   = payload.get("line_items", [])
        tax_rate = float(payload.get("tax_rate", 0))
        subtotal = sum(i.get("quantity", 0) * i.get("unit_price", 0) for i in items)
        total_amt = payload.get("total") or subtotal * (1 + tax_rate)

        invoices.append({
            "invoice_no":  r["invoice_no"],
            "date":        r["created_at"][:10],
            "client":      client,
            "total":       total_amt,
            "currency_symbol": payload.get("currency_symbol", "£"),
            "pdf_path":    r["pdf_path"],
        })

    total_pages = max((total + per_page - 1) // per_page, 1)
    return render_template(
        "history.html",
        invoices=invoices,
        q=q,
        page=page,
        total_pages=total_pages,
        has_prev=page > 1,
        has_next=page < total_pages,
    )

from db import get_invoice, delete_invoice

@app.post("/history/delete/<invoice_no>")
@login_required
@require_csrf
@limiter.limit("30 per minute")
def history_delete(invoice_no):
    row = get_invoice(invoice_no)
    if not row:
        return jsonify({"ok": False, "error": "Not found"}), 404

    # Remove the PDF file if it exists
    pdf_path = row["pdf_path"]
    if pdf_path:
        abs_path = os.path.join(app.static_folder, pdf_path)
        try:
            if os.path.exists(abs_path):
                os.remove(abs_path)
        except Exception:
            app.logger.exception("Failed to remove PDF %s", abs_path)
            # We still proceed to delete the DB row to avoid a ‘ghost’ entry

    delete_invoice(invoice_no)
    return jsonify({"ok": True})

@app.get("/login")
def login():
    if _current_user():
        return redirect(url_for("history"))
    return render_template("login.html", error=None)

@app.post("/login")
def do_login():
    username = (request.form.get("username") or "").strip()
    password = request.form.get("password") or ""

    expected_user = os.getenv("ADMIN_USER", "admin")
    pw_hash = os.getenv("ADMIN_PASSWORD_HASH")
    pw_plain = os.getenv("ADMIN_PASSWORD")

    ok = False
    if pw_hash:
        from werkzeug.security import check_password_hash
        ok = (username == expected_user and check_password_hash(pw_hash, password))
    elif pw_plain:
        ok = (username == expected_user and pw_plain == password)

    if not ok:
        # Avoid leaking which part failed
        return render_template("login.html", error="Invalid credentials"), 401

    session["user"] = expected_user
    session["csrf_token"] = secrets.token_urlsafe(32)
    nxt = request.args.get("next") or url_for("history")
    return redirect(nxt)

@app.post("/logout")
@login_required
@require_csrf
def logout():
    session.clear()
    return redirect(url_for("login"))



# @app.errorhandler(Exception)
# def handle_error(err):
#     logger.exception("Unhandled error")          # stacktrace to log
#     # Browser requests (XHR / fetch) expect JSON
#     if request.accept_mimetypes.accept_json:
#         return jsonify({"error": "Server error, please try again"}), 500
#     # Fallback HTML
#     return render_template("error.html", message="Unexpected error"), 500

@app.errorhandler(Exception)
def handle_error(err):
    import traceback
    traceback.print_exc()
    app.logger.exception("Unhandled error")

    if request.accept_mimetypes.accept_json:
        return jsonify({"error": "Server error, please try again"}), 500
    return render_template("error.html", message="Unexpected error"), 500


@app.errorhandler(429)
def ratelimit_handler(e):
    # Be conservative: everything we include must be a plain string/number
    limit_str = None
    try:
        # This prints nicely, e.g. "2 per 1 second"
        if hasattr(e, "limit") and hasattr(e.limit, "limit"):
            limit_str = str(e.limit.limit)
    except Exception:
        limit_str = None

    return jsonify({
        "error":  "Too many requests",
        "detail": str(getattr(e, "description", "Rate limit exceeded")),
        "limit":  limit_str,   # string or None
    }), 429



if __name__ == '__main__':
    app.run(debug=True)