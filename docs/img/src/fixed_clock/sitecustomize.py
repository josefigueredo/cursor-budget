"""Captures only: every python3 that capturas.sh starts reads the same clock, the tests' (2026-10-24 10:53 -03).

Python imports sitecustomize before anything else, so cbudget's `now=time.time` defaults bind to this one too. With it a
rerun rewrites no capture whose commands and code did not change."""
import time

time.time = lambda: 1792850000.0
