import subprocess
import sys


def test_engine_imports_no_framework_or_database():
    code = (
        "import sys, pathguard_engine; "
        "bad = [m for m in ('fastapi', 'sqlalchemy', 'starlette', 'psycopg', 'httpx', 'app')"
        " if m in sys.modules]; print(','.join(bad))"
    )
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=True)
    assert out.stdout.strip() == ""
