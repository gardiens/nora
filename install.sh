# 1. Create the environment with all system requirements
conda create -n nora -c conda-forge \
    python=3.11 \
    pip \
    "nodejs>=20,<21" \
    git \
    -y

# 2. Activate it
conda activate nora

# 3. Check versions
python --version
pip --version
node --version
npm --version
git --version


# 5. Update packaging tools
python -m pip install --upgrade pip setuptools wheel

# 6. Install NoRA in editable mode
python -m pip install -e .