# Dedicated project environment

This project uses `telecom_de`, isolated from Anaconda `base` and `omantel`.
Its interpreter is `<CONDA_ROOT>/envs/telecom_de/python.exe` on Windows;
`<CONDA_ROOT>` represents your own Anaconda installation directory.

Verified on 2026-11-08 after approved creation and dependency installation:
Python 3.11.18, Pandas 3.1.1, pytest 9.2.0, ipykernel 7.4.0.
`pip check` returned `No broken requirements found.` All seven Phase 1 tests
passed in 2.18 seconds. The actual source loaded as `(66, 9)` and its SHA-256
remained `12669dca1700108add4eeab7e5ff15dba6e1a32d4ec41e2e8cc61d1f7af28ddd`.
Install commands targeted only `telecom_de`; no packages were installed into or
updated in `base` or `omantel`. Phase 2 has not started.

To reproduce on another machine, first check existing environments:

```powershell
conda env list
# Only create if absent:
conda create --name telecom_de python=3.11 pip --yes
conda activate telecom_de
python -m pip install -r requirements.txt ipykernel
```

`ipykernel` supplies notebook execution support; it is separate from the pipeline's
runtime/test requirements. No packages need to be borrowed from another environment.

## VS Code and notebooks

Workspace settings in `.vscode/settings.json` set the default interpreter and
enable pytest discovery in `tests`. The interpreter path is local to this Windows
installation; other developers should select their own `telecom_de` interpreter.

If VS Code has previously stored a different interpreter, the default setting
does not override that selection. Open **Ctrl+Shift+P → Python: Select Interpreter
→ Enter interpreter path** and select your own environment's interpreter. Open a new terminal after
selecting it. In a notebook choose **Select Kernel → Python Environments →
telecom_de**. Check `import sys; print(sys.executable)` to confirm the selection.
The live VS Code interpreter/kernel selection has not been inspected by automation.

For a standalone Jupyter Notebook server that does not discover the environment,
register its kernel (optional; writes a user-level kernelspec):

```powershell
python -m ipykernel install --user --name telecom_de --display-name "Python (telecom_de)"
```

## Run Phase 1

```powershell
conda activate telecom_de
python -m pip check
python -m telecom_pipeline.ingestion data/raw/tra-open-data-traffic.csv
python -m pytest -q
```

If Conda is unavailable in the VS Code terminal, invoke the interpreter directly:

```powershell
& '<CONDA_ROOT>/envs/telecom_de/python.exe' -m pytest -q
```
