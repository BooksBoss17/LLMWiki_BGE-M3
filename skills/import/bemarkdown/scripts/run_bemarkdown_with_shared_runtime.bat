@echo off
python "%~dp0..\..\..\_shared\model-tools\scripts\model_runtime.py" run --id marker-pdf --script "%~dp0batch_convert_all.py" -- %*
