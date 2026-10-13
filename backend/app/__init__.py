import sys

# בטרמינל של Windows ברירת המחדל אינה UTF-8, ואז עברית ו-✓ גורמים לשגיאה בהדפסה.
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure") and (_stream.encoding or "").lower() not in ("utf-8", "utf8"):
        _stream.reconfigure(encoding="utf-8")
