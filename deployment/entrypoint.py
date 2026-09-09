"""Read a mounted token without writing it into image metadata or process arguments."""

import os
import sys
from pathlib import Path

from document_files.cli import main

path = Path(os.environ.get("DOCUMENT_FILES_SERVER_TOKEN_FILE", "/run/secrets/server_token"))
token = path.read_text(encoding="ascii").strip()
if len(token) < 32 or any(c.isspace() for c in token):
    raise SystemExit("invalid server token file")
os.environ["DOCUMENT_FILES_SERVER_TOKEN"] = token
sys.argv = ["document-files", "serve", *sys.argv[1:]]
main()
