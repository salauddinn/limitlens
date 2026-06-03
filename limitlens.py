#!/usr/bin/env python3
"""
LimitLens - Unified AI Tool Quota and Status Monitor.

This is the main entry point shim for the LimitLens CLI application.
It provides a unified interface for checking quotas across tools like
Codex, Amp, OpenCode, Pi, Antigravity, Pioneer, and others.

Usage:
    limitlens [options]

For more information, see the README.md or run `limitlens --help`.
"""

if __name__ == "__main__":
    from limitlens import main
    main()
