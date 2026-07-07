import os
BASE_DIR = os.path.dirname(os.path.abspath("main.py"))
fd = os.path.join(BASE_DIR, "frontend", "dist")
print("BASE_DIR:", BASE_DIR)
print("FRONTEND_DIST:", fd)
print("isdir:", os.path.isdir(fd))
print("index:", os.path.isfile(os.path.join(fd, "index.html")))
