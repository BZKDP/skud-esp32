#!/bin/bash
# Чистая CPU-only установка движка счётчика на Pi (без NVIDIA/CUDA хлама).
pkill -9 -f people-counter 2>/dev/null
sleep 2
rm -rf /opt/people-counter/venv "$HOME/.cache/pip"
python3 -m venv /opt/people-counter/venv
/opt/people-counter/venv/bin/pip install -q --upgrade pip
echo "=== ставлю CPU torch ==="
/opt/people-counter/venv/bin/pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
echo "=== ставлю ultralytics ==="
/opt/people-counter/venv/bin/pip install ultralytics opencv-python-headless lap
echo DONE_INSTALL
