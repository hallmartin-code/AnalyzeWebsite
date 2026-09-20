web: gunicorn --chdir analyzewebsite_web app:app --bind 0.0.0.0:$PORT --workers 2 --worker-class gthread --threads 4 --timeout 300 --graceful-timeout 30
