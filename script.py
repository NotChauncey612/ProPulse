import pylance

python - <<'PY'
from pathlib import Path
for p in Path('classes').glob('*.py'):
    data = p.read_bytes()
    if b'\x00' in data:
        print(p)
PY