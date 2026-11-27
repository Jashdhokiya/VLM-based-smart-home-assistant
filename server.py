#!/usr/bin/env python3
"""INOT Mark1 — Web server entry point."""
import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

if __name__ == "__main__":
    port = int(os.getenv("DASHBOARD_PORT", "8000"))
    host = os.getenv("DASHBOARD_HOST", "0.0.0.0")
    print(f"\n{'='*56}")
    print(f"  INOT Mark1 Smart Home Server")
    print(f"  Dashboard:    http://localhost:{port}")
    print(f"  Digital Twin: http://localhost:{port}/twin")
    print(f"  API:          http://localhost:{port}/api/status")
    print(f"{'='*56}\n")
    uvicorn.run("src.web.server:app", host=host, port=port, reload=False)
