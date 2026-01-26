"""
Gunicorn configuration file for DocSmart API production deployment.
"""

import multiprocessing
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '5000')}"

workers = int(os.environ.get('GUNICORN_WORKERS', multiprocessing.cpu_count() * 2 + 1))

worker_class = 'sync'

timeout = 120

keepalive = 5

max_requests = 1000
max_requests_jitter = 50

preload_app = False

accesslog = '-'
errorlog = '-'
loglevel = os.environ.get('LOG_LEVEL', 'info')

proc_name = 'docsmart-api'

def on_starting(server):
    print(f"Starting Gunicorn server for DocSmart API")

def on_reload(server):
    print(f"Reloading Gunicorn server")

def when_ready(server):
    print(f"Gunicorn server is ready. Spawning workers")
