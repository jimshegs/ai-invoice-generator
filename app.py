from flask import Flask, url_for, render_template, request, jsonify, make_response
import re
from datetime import datetime
from dateutil import parser
import json
#from xhtml2pdf import pisa
from playwright.sync_api import sync_playwright
from io import BytesIO
import openai
import os
from dotenv import load_dotenv
from werkzeug.utils import secure_filename


# Load environment variables
load_dotenv()

app = Flask(__name__)

from db import init_db, next_invoice_no, persist_invoice
init_db()

# right after your Flask app is created (or in a config section):
UPLOAD_FOLDER = os.path.join(app.static_folder, 'uploads')
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER

# Configure OpenAI
openai.api_key = os.getenv('OPENAI_API_KEY')

class AIInvoiceParser:
    def __init__(self):
        # Keep the regex parser as fallback
        self.fallback_parser = InvoiceParser()
    
    def parse(self, text):
        """Use OpenAI function calling to parse invoice description with structured output"""
        print(f"🤖 AI Parser called with: '{text}'")
        
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



                        # "properties": {
                        #     "client": {
                        #         "type": "string",
                        #         "description": "The client or company name. If not explicitly mentioned, use 'Client Name'"
                        #     },
                        #     "amount": {
                        #         "type": "number",
                        #         "description": "The total amount in the given currency (USD, GBP, EUR, etc.). Calculate from hours * rate if given. Use 0 if unclear."
                        #     },
                        #     "date": {
                        #         "type": "string",
                        #         "description": "The invoice date in format 'Month DD, YYYY'. Use today's date if not specified."
                        #     },
                        #     "description": {
                        #         "type": "string", 
                        #         "description": "A brief description of the services provided. Default to 'Professional services' if unclear."
                        #     },
                        #     "confidence": {
                        #         "type": "number",
                        #         "description": "Confidence level from 0-1 indicating how certain the extraction is"
                        #     }
                        # },
                        #"required": ["client", "amount", "date", "description", "confidence"]
                    }
                }
            }

            print("🚀 Calling OpenAI API with function calling...")
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

                
                print(f"✅ OpenAI function call result: {function_args}")
                
                # # Build result with validation
                # result = {
                #     'client': str(function_args.get('client', 'Client Name')).strip(),
                #     'amount': float(function_args.get('amount', 0)),
                #     'date': str(function_args.get('date', datetime.now().strftime('%B %d, %Y'))).strip(),
                #     'description': str(function_args.get('description', 'Professional services')).strip(),
                #     'confidence': float(function_args.get('confidence', 0.5)),
                #     'raw_text': text,
                #     'ai_processed': True
                # }
                
                print(f"🎯 AI successfully parsed with confidence {result['confidence']}: {result}")
                return result
            else:
                raise ValueError("No function call in response")
                
        except Exception as e:
            print(f"❌ OpenAI API error: {e}")
            # Fall back to regex parser
            print("🔄 Falling back to regex parser...")
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
def parse_invoice():
    data = request.get_json()
    text = data.get('text', '')
    
    if not text.strip():
        return jsonify({'error': 'Please provide invoice description'})
    
    parsed_data = parser_instance.parse(text)
    return jsonify(parsed_data)

@app.route('/generate', methods=['POST'])
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
    if file and file.filename:
        fname = secure_filename(file.filename)
        save_path = os.path.join(app.config['UPLOAD_FOLDER'], fname)
        file.save(save_path)
        logo_url = url_for('static', filename=f'uploads/{fname}', _external=False)

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


# @app.route('/generate', methods=['POST'])
# def generate_invoice():
#     data = request.get_json(force=True) or {}

#     # ---------- 1. basic fields ----------
#     client          = data.get('client', 'Client')
#     client_address  = data.get('client_address', '')
#     sender          = data.get('sender', 'Your Business')
#     sender_address  = data.get('sender_address', '')
#     invoice_no      = data.get('invoice_no', f"INV-{datetime.now():%Y%m%d-%H%M}")
#     invoice_date    = data.get('invoice_date', datetime.now().date().isoformat())
#     due_date        = data.get('due_date', '')
#     payment_terms   = data.get('payment_terms', '')
#     currency        = data.get('currency_symbol', '£')
#     tax_rate        = float(data.get('tax_rate', 0.0))      # 0-1

    


#     # ---------- 2. line-items & totals ----------
#     items = data.get('line_items', [])
#     subtotal   = sum(i['quantity'] * i['unit_price'] for i in items)
#     tax_amount = subtotal * tax_rate
#     total      = subtotal + tax_amount

#     return render_template(
#         'invoice.html',
#         client=client,
#         client_address=client_address,
#         sender=sender,
#         sender_address=sender_address,
#         invoice_no=invoice_no,
#         invoice_date=invoice_date,
#         due_date=due_date,
#         payment_terms=payment_terms,
#         currency_symbol=currency,
#         line_items=items,
#         subtotal=subtotal,
#         tax_rate=tax_rate,
#         tax_amount=tax_amount,
#         total=total,
#     )


# @app.route('/generate', methods=['POST'])
# def generate_invoice():
#     data = request.get_json()
    
#     # Add invoice number and your business details
#     invoice_data = {
#         'invoice_number': f"INV-{datetime.now().strftime('%Y%m%d')}-001",
#         'your_name': "Your Name",
#         'your_email': "your.email@example.com",
#         'your_address': "Your Address\nCity, State ZIP",
#         'client': data.get('client'),
#         'amount': data.get('amount'),
#         'date': data.get('date'),
#         'description': data.get('description'),
#         'due_date': data.get('date')  # For now, same as invoice date
#     }
    
#     return render_template('invoice.html', **invoice_data)

# @app.route('/download-pdf', methods=['POST'])
# def download_pdf():
#     data = request.get_json()
    
#     # Prepare invoice data
#     invoice_data = {
#         'invoice_number': f"INV-{datetime.now().strftime('%Y%m%d')}-001",
#         'your_name': "Your Name",
#         'your_email': "your.email@example.com",
#         'your_address': "Your Address\nCity, State ZIP",
#         'client': data.get('client'),
#         'amount': data.get('amount'),
#         'date': data.get('date'),
#         'description': data.get('description'),
#         'due_date': data.get('date')
#     }
    
#     # Render the PDF template
#     html_content = render_template('invoice_pdf.html', **invoice_data)
    
#     # Create PDF
#     result = BytesIO()
#     pdf = pisa.pisaDocument(BytesIO(html_content.encode("UTF-8")), result)
    
#     if not pdf.err:
#         # Create response
#         response = make_response(result.getvalue())
#         response.headers['Content-Type'] = 'application/pdf'
#         response.headers['Content-Disposition'] = f'attachment; filename="invoice-{invoice_data["invoice_number"]}.pdf"'
#         return response
#     else:
#         return "Error generating PDF", 500


@app.route('/download-pdf', methods=['POST'])
def download_pdf():
    data = request.get_json(force=True) or {}

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

    # if request.content_type.startswith('multipart/form-data'):
    #     payload = json.loads(request.form['payload'])
    #     file = request.files.get('logo')
    # else:                                         # fallback for JSON callers
    #     payload = request.get_json(force=True) or {}
    #     file = None

    # logo_url = None
    # if file and file.filename:
    #     fname = secure_filename(file.filename)
    #     save_path = os.path.join(app.config['UPLOAD_FOLDER'], fname)
    #     file.save(save_path)
    #     logo_url = url_for('static', filename=f'uploads/{fname}', _external=False)

    # # 2 -- compute totals (same as before)
    # items = payload.get('line_items', [])
    # tax_rate  = float(payload.get('tax_rate', 0.0))
    # subtotal  = sum(i['quantity']*i['unit_price'] for i in items)
    # tax_amt   = subtotal * tax_rate
    # total     = subtotal + tax_amt

    # html = render_template(
    #     'invoice_pdf2.html',  # or 'invoice_pdf.html' if you prefer
    #     **payload,
    #     logo_url     = logo_url,
    #     subtotal     = subtotal,
    #     # tax_rate     = tax_rate,
    #     tax_amount   = tax_amt,
    #     total        = total
    # )

    # ---------- 4. Convert HTML → PDF with Playwright (PERFECT rendering) ----------
    try:
        with sync_playwright() as p:
            # Launch Chrome browser (headless)
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            
            # Set content and wait for it to load
            page.set_content(html, wait_until='networkidle')
            
            # Generate PDF with perfect rendering
            pdf_bytes = page.pdf(
                format='A4',
                print_background=True,  # CRITICAL: renders backgrounds and colors
                margin={
                    'top': '0mm',
                    'bottom': '0mm', 
                    'left': '0mm',
                    'right': '0mm'
                },
                prefer_css_page_size=True
            )
            
            browser.close()
            
        # Create response
        response = make_response(pdf_bytes)
        response.headers['Content-Type'] = 'application/pdf'
        response.headers['Content-Disposition'] = f'attachment; filename="{invoice_no}.pdf"'
        return response
        
    except Exception as e:
        print(f"Playwright error: {e}")
        return f"Error generating PDF: {str(e)}", 500



    # ---------- 4. Convert HTML → PDF ----------
    # result = BytesIO()
    # pdf = pisa.pisaDocument(BytesIO(html.encode("utf-8")), result)

    # if pdf.err:
    #     return "Error generating PDF", 500

    # response = make_response(result.getvalue())
    # response.headers['Content-Type'] = 'application/pdf'
    # response.headers['Content-Disposition'] = f'attachment; filename=\"{invoice_no}.pdf\"'
    # return response


if __name__ == '__main__':
    app.run(debug=True)