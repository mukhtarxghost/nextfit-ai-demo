#!/usr/bin/env python3
"""
Generate a professional PDF from PROJECT_NOTES.md
Uses markdown for MD->HTML conversion and xhtml2pdf for HTML->PDF.
"""

import re
import sys
from pathlib import Path
from io import BytesIO

import markdown
from xhtml2pdf import pisa

# ─── Configuration ───────────────────────────────────────────────────────────

REPO_ROOT = Path(__file__).parent
SOURCE_MD = REPO_ROOT / "PROJECT_NOTES.md"
OUTPUT_PDF = REPO_ROOT / "Next_Fit_Voice_Project_Notes.pdf"

# ─── Security Scan ───────────────────────────────────────────────────────────

def scan_for_secrets(content):
    patterns = [
        (r'gsk_[A-Za-z0-9]{20,}', 'Groq API Key'),
        (r'sk_[a-f0-9]{20,}', 'ElevenLabs API Key'),
        (r'ghp_[A-Za-z0-9]{36}', 'GitHub PAT'),
    ]
    findings = []
    for pattern, name in patterns:
        matches = re.findall(pattern, content)
        if matches:
            findings.append((name, matches))
    return findings


def sanitize_content(content):
    content = re.sub(r'gsk_[A-Za-z0-9]{20,}', 'gsk_XXXXXXXXXXXXXXXXXXXXXXXX', content)
    content = re.sub(r'sk_[a-f0-9]{20,}', 'sk_XXXXXXXXXXXXXXXXXXXXXXXX', content)
    return content


# ─── Markdown Processing ─────────────────────────────────────────────────────

def preprocess_markdown(content):
    """Remove the original TOC (we generate our own)."""
    lines = content.split('\n')
    new_lines = []
    in_toc = False
    skip_next_hr = False

    for line in lines:
        if line.strip() == '## TABLE OF CONTENTS':
            in_toc = True
            continue
        if in_toc and line.strip() == '---':
            in_toc = False
            skip_next_hr = True
            continue
        if in_toc:
            continue
        if skip_next_hr and line.strip() == '---':
            skip_next_hr = False
            continue
        new_lines.append(line)

    return '\n'.join(new_lines)


def extract_headings_for_toc(html):
    """Extract h2/h3 headings for table of contents."""
    toc_items = []
    pattern = r'<(h[23])\s*id="([^"]*)"[^>]*>(.*?)</\1>'
    for match in re.finditer(pattern, html):
        level = int(match.group(1)[1])
        heading_id = match.group(2)
        text = re.sub(r'<[^>]+>', '', match.group(3)).strip()
        toc_items.append({'level': level, 'id': heading_id, 'text': text})
    return toc_items


def build_toc_html(toc_items):
    """Build a table of contents."""
    if not toc_items:
        return ""
    html = '<div class="toc-page">\n'
    html += '<p class="toc-heading">TABLE OF CONTENTS</p>\n'
    for item in toc_items:
        indent = '&nbsp;&nbsp;&nbsp;&nbsp;' if item['level'] == 3 else ''
        css_class = 'toc-entry-sub' if item['level'] == 3 else 'toc-entry'
        html += '<p class="%s">%s%s</p>\n' % (css_class, indent, item['text'])
    html += '</div>\n'
    return html


# ─── CSS (xhtml2pdf compatible) ─────────────────────────────────────────────

def get_css():
    return """
/* ── Page ── */
@page {
    size: A4;
    margin: 2cm 2cm 2.5cm 2cm;
}

/* ── Base ── */
body {
    font-family: Helvetica, Arial, sans-serif;
    font-size: 10pt;
    line-height: 1.5;
    color: #1a1a1a;
}

/* ── Title Page ── */
.title-page {
    page-break-after: always;
    text-align: center;
}

.title-spacer {
    height: 140pt;
}

.doc-title {
    font-size: 30pt;
    font-weight: bold;
    color: #111111;
    margin-bottom: 6pt;
}

.doc-subtitle {
    font-size: 15pt;
    font-weight: normal;
    color: #444444;
    margin-bottom: 4pt;
}

.doc-divider {
    width: 60pt;
    height: 2pt;
    background-color: #2563eb;
    margin: 24pt auto;
    border: none;
}

.doc-tagline {
    font-size: 11pt;
    color: #777777;
    font-style: italic;
    margin-bottom: 40pt;
}

.doc-meta {
    font-size: 10pt;
    color: #555555;
    margin-top: 100pt;
    line-height: 2;
}

.doc-meta-label {
    font-weight: bold;
    color: #222222;
}

/* ── Table of Contents Page ── */
.toc-page {
    page-break-after: always;
}

.toc-heading {
    font-size: 16pt;
    font-weight: bold;
    color: #111111;
    border-bottom: 2pt solid #2563eb;
    padding-bottom: 4pt;
    margin-bottom: 12pt;
}

.toc-entry {
    font-size: 10pt;
    font-weight: bold;
    color: #1a1a1a;
    margin: 3pt 0;
    padding: 2pt 0;
    border-bottom: 0.5pt solid #e0e0e0;
}

.toc-entry-sub {
    font-size: 9pt;
    font-weight: normal;
    color: #444444;
    margin: 1pt 0 1pt 12pt;
    padding: 1pt 0;
}

/* ── Headings ── */
h1 {
    font-size: 20pt;
    font-weight: bold;
    color: #111111;
    margin-top: 24pt;
    margin-bottom: 8pt;
    border-bottom: 2pt solid #2563eb;
    padding-bottom: 4pt;
}

h2 {
    font-size: 14pt;
    font-weight: bold;
    color: #1a1a1a;
    margin-top: 20pt;
    margin-bottom: 6pt;
    border-bottom: 1pt solid #cccccc;
    padding-bottom: 3pt;
}

h3 {
    font-size: 11.5pt;
    font-weight: bold;
    color: #222222;
    margin-top: 14pt;
    margin-bottom: 5pt;
}

h4 {
    font-size: 10.5pt;
    font-weight: bold;
    color: #333333;
    margin-top: 10pt;
    margin-bottom: 4pt;
}

/* ── Paragraphs ── */
p {
    margin: 3pt 0 6pt 0;
}

/* ── Lists ── */
ul, ol {
    margin: 3pt 0 6pt 16pt;
    padding: 0;
}

li {
    margin-bottom: 2pt;
    line-height: 1.45;
}

/* ── Tables ── */
table {
    width: 100%;
    border-collapse: collapse;
    margin: 6pt 0 10pt 0;
    font-size: 8.5pt;
}

th {
    background-color: #eef2ff;
    font-weight: bold;
    text-align: left;
    padding: 5pt 6pt;
    border: 0.5pt solid #bbbbbb;
}

td {
    padding: 4pt 6pt;
    border: 0.5pt solid #dddddd;
    vertical-align: top;
    line-height: 1.4;
}

tr:nth-child(even) td {
    background-color: #f9f9f9;
}

/* ── Code ── */
code {
    font-family: Courier, monospace;
    font-size: 8pt;
    background-color: #f0f0f0;
    padding: 0pt 2pt;
    color: #333333;
}

pre {
    background-color: #f5f5f5;
    border: 0.5pt solid #dddddd;
    padding: 6pt 8pt;
    margin: 6pt 0 10pt 0;
    font-size: 7.5pt;
    line-height: 1.4;
    white-space: pre-wrap;
    word-wrap: break-word;
}

pre code {
    background-color: transparent;
    padding: 0;
    color: #222222;
    font-size: 7.5pt;
}

/* ── Blockquotes ── */
blockquote {
    border-left: 3pt solid #2563eb;
    margin: 8pt 0;
    padding: 4pt 10pt;
    background-color: #eef2ff;
    color: #333333;
}

blockquote p {
    margin: 2pt 0;
}

/* ── Horizontal Rules ── */
hr {
    border: none;
    border-top: 0.5pt solid #cccccc;
    margin: 14pt 0;
}

/* ── Strong / Emphasis ── */
strong {
    font-weight: bold;
}

em {
    font-style: italic;
}

/* ── Links ── */
a {
    color: #2563eb;
    text-decoration: none;
}
"""


# ─── Title Page ──────────────────────────────────────────────────────────────

def build_title_page():
    return """
<div class="title-page">
    <div class="title-spacer"></div>
    <p class="doc-title">Next Fit Voice</p>
    <p class="doc-subtitle">Technical Project Notes &amp; Setup Guide</p>
    <hr class="doc-divider"/>
    <p class="doc-tagline">Technical Handoff &amp; Learning Documentation</p>
    <div class="doc-meta">
        <p><span class="doc-meta-label">Prepared for:</span> Next Fit Studio</p>
        <p><span class="doc-meta-label">Prepared by:</span> Vantix Automation Solutions</p>
        <p><span class="doc-meta-label">Document Version:</span> 1.0</p>
        <p><span class="doc-meta-label">Date:</span> September 2026</p>
    </div>
</div>
"""


# ─── Main ────────────────────────────────────────────────────────────────────

def main():
    print("Reading PROJECT_NOTES.md...")
    raw_content = SOURCE_MD.read_text(encoding='utf-8')

    print("Running security scan...")
    findings = scan_for_secrets(raw_content)
    if findings:
        print("  WARNING: Potential secrets found (will be sanitized):")
        for name, matches in findings:
            print("    - %s: %d occurrence(s)" % (name, len(matches)))
    else:
        print("  No secrets found.")

    content = sanitize_content(raw_content)
    content = preprocess_markdown(content)

    print("Converting Markdown to HTML...")
    html_body = markdown.markdown(
        content,
        extensions=['tables', 'fenced_code', 'toc']
    )

    # Extract headings and build TOC
    toc_items = extract_headings_for_toc(html_body)
    toc_html = build_toc_html(toc_items)

    # Assemble HTML
    css = get_css()
    title_page = build_title_page()

    full_html = """<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8"/>
    <title>Next Fit Voice - Technical Project Notes</title>
    <style>
%s
    </style>
</head>
<body>
%s
%s
%s
</body>
</html>""" % (css, title_page, toc_html, html_body)

    # Write debug HTML
    html_path = REPO_ROOT / "project_notes_debug.html"
    html_path.write_text(full_html, encoding='utf-8')
    print("  Debug HTML saved to project_notes_debug.html")

    # Generate PDF
    print("Generating PDF...")
    result = BytesIO()
    pdf_status = pisa.CreatePDF(
        src=full_html,
        dest=result,
        encoding='utf-8',
    )

    if pdf_status.err:
        print("  ERROR: PDF generation failed with %d errors." % pdf_status.err)
        sys.exit(1)

    OUTPUT_PDF.write_bytes(result.getvalue())
    file_size = OUTPUT_PDF.stat().st_size
    print("  PDF generated: %s (%d bytes)" % (OUTPUT_PDF.name, file_size))

    # Page count
    try:
        raw = result.getvalue()
        page_count = raw.count(b'/Type /Page') - raw.count(b'/Type /Pages')
        print("  Approximate page count: %d" % page_count)
    except Exception:
        pass

    print("\nDone!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
