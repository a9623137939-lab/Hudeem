import sys
import traceback
import os

def main():
    print("=== STARTING DEBUG ===")
    print(f"Python version: {sys.version}")
    print(f"Current directory: {os.getcwd()}")
    print("Attempting imports...")
    
    try:
        import sqlite3
        print("✓ sqlite3")
    except Exception as e:
        print(f"✗ sqlite3: {e}")
        raise
    
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        print("✓ apscheduler")
    except Exception as e:
        print(f"✗ apscheduler: {e}")
        raise
    
    try:
        from flask import Flask
        print("✓ flask")
    except Exception as e:
        print(f"✗ flask: {e}")
        raise
    
    try:
        from telegram import Bot
        print("✓ telegram")
    except Exception as e:
        print(f"✗ telegram: {e}")
        raise
    
    try:
        from telegram.ext import Application, CommandHandler, ContextTypes
        print("✓ telegram.ext")
    except Exception as e:
        print(f"✗ telegram.ext: {e}")
        raise
    
    print("All imports successful. Creating bot...")
    
    TOKEN = os.getenv("BOT_TOKEN")
    if not TOKEN:
        print("ERROR: BOT_TOKEN not set")
        sys.exit(1)
    
    print("Token found, building application...")
    try:
        app = Application.builder().token(TOKEN).build()
        print("Application built successfully")
    except Exception as e:
        print(f"Error building application: {e}")
        traceback.print_exc()
        sys.exit(1)
    
    print("DEBUG DONE. Exiting gracefully.")
    sys.exit(0)

if __name__ == "__main__":
    main()
