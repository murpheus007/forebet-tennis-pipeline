#!/usr/bin/env python3
"""
Load JSON files into database - entry point for ETL.
"""
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

from src.etl import ETL
import argparse

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Load JSON files into PostgreSQL")
    parser.add_argument("--pattern", default="*_historical.json", help="File pattern to process")
    parser.add_argument("--file", help="Process single file")
    args = parser.parse_args()
    
    etl = ETL()
    if args.file:
        from pathlib import Path
        etl.process_file(Path(args.file))
    else:
        etl.process_all(args.pattern)