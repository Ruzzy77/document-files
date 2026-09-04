# Vendored host fallback

`olefile.py` is the unmodified pure-Python module from `olefile` 0.47. It is included so the
OpenAI host can run the HWP specification parser without installing packages during a document
task. Normal installed runtimes continue to prefer their locked `olefile` dependency.

The upstream project is <https://github.com/decalage2/olefile>. Its BSD-style license is preserved
in `OLEFILE_LICENSE.md`.
