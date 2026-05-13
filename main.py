import sys
from PyQt6.QtWidgets import QApplication
from app.main_window import MainWindow


def main():
    app = QApplication(sys.argv)
    app.setApplicationName("PDF Editor")
    win = MainWindow()
    win.show()
    if len(sys.argv) > 1:
        win.open_path(sys.argv[1])
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
