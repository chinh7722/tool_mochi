"""Open the standalone, local SSH connection and key manager."""
from pathlib import Path
import sys
root=Path(__file__).resolve().parent
sys.path[:]=[p for p in sys.path if p and Path(p).resolve()!=root]
sys.path.insert(0,str(root/'src'))
if __name__=='__main__':
    from PySide6.QtWidgets import QApplication
    from PySide6.QtGui import QFont
    from gui.branding import WORKSPACE_STYLE
    from gui.ssh_manager import SSHManager
    app=QApplication(sys.argv);app.setFont(QFont('Segoe UI',10));app.setStyleSheet(WORKSPACE_STYLE)
    window=SSHManager(root/'data',root);window.show();sys.exit(app.exec())
