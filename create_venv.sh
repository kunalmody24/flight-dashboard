python3 -m venv .venv

# Activate virtual environment
source .venv/bin/activate

# Upgrade pip
python -m pip install --upgrade pip

# Install uv
pip install uv

# Install project requirements using uv
uv pip install -r requirements.txt

echo "Setup complete!"
echo "Activate your environment with: source .venv/bin/activate"