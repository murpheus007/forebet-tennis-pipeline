#!/usr/bin/env python3
"""
Setup database - creates tables and indexes.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.database import init_database

if __name__ == "__main__":
    print("Initializing database...")
    init_database()
    print("Done!")