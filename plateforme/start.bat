@echo off
cd /d "%~dp0"
echo =============================================
echo  Estimation Immobiliere Casablanca - API
echo =============================================

REM Verifier si les dependances sont installees
python -c "import catboost, fastapi, uvicorn" 2>nul
if errorlevel 1 (
    echo Installation des dependances...
    pip install -r requirements.txt
)

echo.
echo Demarrage de l'API sur http://localhost:8000
echo Interface disponible sur http://localhost:8000
echo Documentation API : http://localhost:8000/docs
echo.
echo Appuyez sur Ctrl+C pour arreter.
echo.

python -m uvicorn api:app --host 0.0.0.0 --port 8000 --reload
pause
