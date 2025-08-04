# pdf_utils.py
from playwright.sync_api import sync_playwright

def html_to_pdf(html: str) -> bytes:
    """Render HTML to PDF bytes using Playwright Chromium."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            page = browser.new_page()
            page.set_content(html, wait_until='networkidle')
            pdf_bytes = page.pdf(
                format='A4',
                print_background=True,          # keep backgrounds/colors
                margin={'top':'0mm','bottom':'0mm','left':'0mm','right':'0mm'},
                prefer_css_page_size=True
            )
            return pdf_bytes
        finally:
            browser.close()
