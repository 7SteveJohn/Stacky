"""
legacy_migrator.py - Backward-compatible shim redirecting to asset_migrator.py
Preserves 100% compatibility for external callers, tests and legacy configs.
"""

from asset_migrator import *
