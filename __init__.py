from aqt import mw
from aqt.qt import QAction
from importlib import reload

def main():
    from . import importer
    reload(importer)
    importer.import_highlights()

action = QAction('Import Smart Kindle highlights...', mw)
action.setShortcut("Ctrl+K")
action.triggered.connect(main)
mw.form.menuTools.addAction(action)
