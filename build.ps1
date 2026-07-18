Remove-Item -Recurse -Force build, dist -ErrorAction SilentlyContinue
python -m PyInstaller MyToolBox.spec
