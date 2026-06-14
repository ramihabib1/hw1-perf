#!/usr/bin/env python3
"""Build writeup/SUBMISSION.pdf from writeup/SUBMISSION.md.
Markdown -> styled HTML -> (headless Chrome) PDF. Run: python3 scripts/build_submission_pdf.py
"""
import os, subprocess, sys
import markdown

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MD = os.path.join(ROOT, "writeup", "SUBMISSION.md")
HTML = os.path.join(ROOT, "writeup", "SUBMISSION.html")
PDF = os.path.join(ROOT, "writeup", "SUBMISSION.pdf")
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

CSS = """
@page { size: A4; margin: 18mm 16mm; }
* { box-sizing: border-box; }
body { font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; font-size: 10.5pt;
       line-height: 1.45; color: #1a1a1a; max-width: 100%; }
h1 { font-size: 18pt; border-bottom: 2px solid #333; padding-bottom: 4px; margin-top: 22px;
     page-break-before: always; }
h1:first-of-type { page-break-before: avoid; }
h2 { font-size: 13pt; margin-top: 18px; border-bottom: 1px solid #ccc; padding-bottom: 2px; }
h3 { font-size: 11.5pt; margin-top: 14px; }
p, li { orphans: 3; widows: 3; }
code { font-family: "SF Mono", "Menlo", "Consolas", monospace; font-size: 8.6pt;
       background: #f3f3f3; padding: 1px 3px; border-radius: 3px; }
pre { background: #f6f8fa; border: 1px solid #e1e4e8; border-radius: 5px; padding: 8px 10px;
      font-size: 8.2pt; line-height: 1.35; white-space: pre-wrap; word-wrap: break-word;
      page-break-inside: avoid; overflow-wrap: anywhere; }
pre code { background: none; padding: 0; font-size: inherit; }
table { border-collapse: collapse; width: 100%; margin: 10px 0; font-size: 9pt;
        page-break-inside: avoid; }
th, td { border: 1px solid #c8c8c8; padding: 4px 7px; text-align: left; vertical-align: top; }
th { background: #efefef; }
tr:nth-child(even) td { background: #fafafa; }
hr { border: none; border-top: 1px solid #ddd; margin: 18px 0; }
strong { color: #000; }
blockquote { border-left: 3px solid #ccc; margin: 8px 0; padding: 2px 12px; color: #444; }
"""

def main():
    with open(MD, encoding="utf-8") as f:
        text = f.read()
    body = markdown.markdown(text, extensions=["tables", "fenced_code", "sane_lists", "attr_list"])
    html = (f'<!DOCTYPE html><html lang="en"><head><meta charset="utf-8">'
            f"<style>{CSS}</style></head><body>{body}</body></html>")
    with open(HTML, "w", encoding="utf-8") as f:
        f.write(html)
    print("wrote", HTML)
    if not os.path.exists(CHROME):
        print("Chrome not found; open the HTML and Print->Save as PDF.", file=sys.stderr)
        return 1
    subprocess.run([CHROME, "--headless", "--disable-gpu", "--no-pdf-header-footer",
                    f"--print-to-pdf={PDF}", f"file://{HTML}"], check=True,
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print("wrote", PDF)
    return 0

if __name__ == "__main__":
    sys.exit(main())
