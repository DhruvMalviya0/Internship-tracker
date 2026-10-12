"""WSGI entry point for the deployed site:  gunicorn "web.wsgi:app" --preload"""
import logging

from web.app import create_app

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)-7s %(name)s %(message)s")
app = create_app()
