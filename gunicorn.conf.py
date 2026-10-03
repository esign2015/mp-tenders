"""Keep slow Sheet requests from blocking the sign-in and readiness endpoints."""
workers = 1  # Account detail jobs and local throttles share this process.
worker_class = 'gthread'
threads = 4
timeout = 120
accesslog = '-'
errorlog = '-'
access_log_format = '%(t)s %(m)s %(U)s status=%(s)s duration=%(L)s'
