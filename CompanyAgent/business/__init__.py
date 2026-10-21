"""Simulated commerce domain. No dependency on HTTP, model SDKs or agent runtimes."""

# This package holds simulated business rules and storage. Importing it must not
# start HTTP/model clients or require Redis/Chroma; callers import services explicitly.
