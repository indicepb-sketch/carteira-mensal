from pathlib import Path
import runpy


APP_PATH = Path(__file__).resolve().parent / "carteira_mensal" / "app" / "streamlit_app_user.py"

runpy.run_path(str(APP_PATH), run_name="__main__")
