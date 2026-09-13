"""Runs only inside the disposable compiler image. Stdout is exclusively PDF."""
import pathlib
import subprocess
import sys

source = sys.stdin.buffer.read(1024 * 1024 + 1)
if len(source) > 1024 * 1024:
    sys.exit("Source exceeds 1 MB.")
pathlib.Path("resume.tex").write_bytes(source)
try:
    with open("compiler.log", "wb") as log:
        for _ in range(2):
            result = subprocess.run(["pdflatex", "-no-shell-escape", "-interaction=nonstopmode", "-halt-on-error", "-file-line-error", "resume.tex"], stdout=log, stderr=log, timeout=15)
            if result.returncode:
                raise ValueError("LaTeX returned an error.")
    pdf = pathlib.Path("resume.pdf")
    if pdf.stat().st_size > 2 * 1024 * 1024:
        raise ValueError("PDF exceeds 2 MB.")
    sys.stdout.buffer.write(pdf.read_bytes())
except Exception as exc:
    sys.stderr.write(str(exc) + "\n")
    with open("compiler.log", "rb") as log:
        log.seek(0, 2)
        log.seek(max(0, log.tell() - 3000))
        sys.stderr.write(log.read().decode(errors="replace"))
    sys.exit(1)
