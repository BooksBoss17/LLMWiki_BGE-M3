@echo off
python "%~dp0..\..\..\_shared\model-tools\scripts\model_runtime.py" run --id faster-whisper-large-v3 --script "%~dp0transcribe_video_batch.py" -- %*
