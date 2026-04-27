import sys
import time
import subprocess
from pathlib import Path
import socket 

from PySide6.QtCore import Qt, QTimer, QObject, QEvent
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QLabel,
    QMainWindow,
    QPushButton,
    QHBoxLayout,
    QVBoxLayout,
    QWidget,
)

import win32api
import win32con
import win32gui
import win32process


# ============================================================
# CONFIGURACIÓN GENERAL
# ============================================================

BASE_DIR = Path(__file__).resolve().parent
PYTHON_EXE = sys.executable

MIDDLEWARE_SCRIPT = BASE_DIR / "middleware.py"
TRACKING_SCRIPT = BASE_DIR / "main.py"

GAME_SCRIPT = BASE_DIR / "game.py"
CALIBRACION_SCRIPT = BASE_DIR / "calibracion.py"

RHYTHM_SCRIPT = BASE_DIR / "rhythm_game.py"
VOICE_LISTENER_SCRIPT = BASE_DIR / "voice_listener.py"

# CAMBIA ESTA RUTA SI TU BUILD ESTÁ EN OTRA CARPETA
UNITY_EXE = BASE_DIR / "build" / "AirDrums.exe"

# Nombres / pistas de ventanas
OPENCV_TITLE_HINTS = [
    "deteccion de bateria",
    "deteccion de batería",
]

UNITY_TITLE_HINTS = [
    "airdrums",
    "air drums",
    "AirDrums",
]

RHYTHM_TITLE_HINTS = [
    "airdrums rhythm highway",
    "airdrums hero",
]

MASK_TITLE_HINTS = [
    "mascara combinada",
    "máscara combinada",
]


# ============================================================
# FUNCIONES WINDOWS
# ============================================================

def enum_visible_windows():
    windows = []

    def callback(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True

            title = win32gui.GetWindowText(hwnd) or ""
            class_name = win32gui.GetClassName(hwnd) or ""
            _, pid = win32process.GetWindowThreadProcessId(hwnd)

            left, top, right, bottom = win32gui.GetWindowRect(hwnd)
            width = max(0, right - left)
            height = max(0, bottom - top)
            area = width * height

            if area > 3000:
                windows.append({
                    "hwnd": hwnd,
                    "pid": pid,
                    "title": title,
                    "class_name": class_name,
                    "width": width,
                    "height": height,
                    "area": area,
                })
        except Exception:
            pass

        return True

    win32gui.EnumWindows(callback, None)
    return windows


def text_matches_hints(text, hints):
    text = (text or "").lower()
    ignorar = ["visual studio", "code", "panel de control", "macrowindow", "explorador"]
    if any(palabra in text for palabra in ignorar):
        return False
    return any(hint.lower() in text for hint in hints)


def find_window_by_pid_and_hints(pid, hints):
    candidates = []

    for window in enum_visible_windows():
        if window["pid"] != pid:
            continue

        title = window["title"]
        if text_matches_hints(title, hints):
            candidates.append(window)

    if not candidates:
        return None

    candidates.sort(key=lambda w: w["area"], reverse=True)
    return candidates[0]["hwnd"]


def find_largest_window_by_pid(pid):
    candidates = [w for w in enum_visible_windows() if w["pid"] == pid]

    if not candidates:
        return None

    candidates.sort(key=lambda w: w["area"], reverse=True)
    return candidates[0]["hwnd"]


def find_window_global_by_hints(hints):
    candidates = []

    for window in enum_visible_windows():
        if text_matches_hints(window["title"], hints):
            candidates.append(window)

    if not candidates:
        return None

    candidates.sort(key=lambda w: w["area"], reverse=True)
    return candidates[0]["hwnd"]


def process_is_alive(process):
    return process is not None and process.poll() is None


def find_window_for_process_or_global(process, hints):
    hwnd = None

    if process_is_alive(process):
        hwnd = find_window_by_pid_and_hints(process.pid, hints)

        if hwnd is None:
            hwnd = find_largest_window_by_pid(process.pid)

    if hwnd is None:
        hwnd = find_window_global_by_hints(hints)

    return hwnd


def hide_window_by_hints(hints):
    hwnd = find_window_global_by_hints(hints)
    if hwnd:
        try:
            win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
        except Exception:
            pass


def embed_window(hwnd, parent_hwnd, width, height):
    if not hwnd:
        return

    try:
        style = win32gui.GetWindowLong(hwnd, win32con.GWL_STYLE)
        exstyle = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)

        style &= ~win32con.WS_POPUP
        style &= ~win32con.WS_CAPTION
        style &= ~win32con.WS_THICKFRAME
        style &= ~win32con.WS_MINIMIZEBOX
        style &= ~win32con.WS_MAXIMIZEBOX
        style &= ~win32con.WS_SYSMENU
        style |= win32con.WS_CHILD
        style |= win32con.WS_VISIBLE

        exstyle &= ~win32con.WS_EX_DLGMODALFRAME
        exstyle &= ~win32con.WS_EX_CLIENTEDGE
        exstyle &= ~win32con.WS_EX_STATICEDGE

        win32gui.SetParent(hwnd, parent_hwnd)
        win32gui.SetWindowLong(hwnd, win32con.GWL_STYLE, style)
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, exstyle)

        win32gui.SetWindowPos(
            hwnd,
            None,
            0,
            0,
            max(1, width),
            max(1, height),
            win32con.SWP_NOZORDER
            | win32con.SWP_NOOWNERZORDER
            | win32con.SWP_FRAMECHANGED
            | win32con.SWP_SHOWWINDOW,
        )

        win32gui.ShowWindow(hwnd, win32con.SW_SHOW)
    except Exception:
        pass

def move_embedded_window(hwnd, width, height):
    if not hwnd:
        return

    try:
        win32gui.MoveWindow(
            hwnd,
            0,
            0,
            max(1, width),
            max(1, height),
            True
        )
    except Exception:
        pass


# ============================================================
# REENVÍO DE TECLADO
# ============================================================

def qt_key_to_vk(qt_key):
    special_keys = {
        int(Qt.Key.Key_Left): win32con.VK_LEFT,
        int(Qt.Key.Key_Right): win32con.VK_RIGHT,
        int(Qt.Key.Key_Up): win32con.VK_UP,
        int(Qt.Key.Key_Down): win32con.VK_DOWN,
        int(Qt.Key.Key_Return): win32con.VK_RETURN,
        int(Qt.Key.Key_Enter): win32con.VK_RETURN,
        int(Qt.Key.Key_Escape): win32con.VK_ESCAPE,
        int(Qt.Key.Key_Space): win32con.VK_SPACE,
        int(Qt.Key.Key_Backspace): win32con.VK_BACK,
        int(Qt.Key.Key_Tab): win32con.VK_TAB,
    }

    qt_key = int(qt_key)

    if qt_key in special_keys:
        return special_keys[qt_key]

    if int(Qt.Key.Key_A) <= qt_key <= int(Qt.Key.Key_Z):
        return ord(chr(qt_key))

    if int(Qt.Key.Key_0) <= qt_key <= int(Qt.Key.Key_9):
        return ord(chr(qt_key))

    return 0


def focus_embedded_window(hwnd):
    if not hwnd:
        return

    try:
        win32gui.BringWindowToTop(hwnd)
        win32gui.SetForegroundWindow(hwnd)
        win32gui.SetFocus(hwnd)
        win32gui.PostMessage(hwnd, win32con.WM_SETFOCUS, 0, 0)
    except Exception:
        pass


def post_key_to_window(hwnd, event, is_down):
    if not hwnd:
        return False

    try:
        vk = int(event.nativeVirtualKey()) if event.nativeVirtualKey() else qt_key_to_vk(event.key())

        if vk == 0:
            return False

        scan_code = win32api.MapVirtualKey(vk, 0)
        lparam = 1 | (scan_code << 16)

        if not is_down:
            lparam |= (1 << 30)
            lparam |= (1 << 31)

        message = win32con.WM_KEYDOWN if is_down else win32con.WM_KEYUP

        focus_embedded_window(hwnd)
        win32gui.PostMessage(hwnd, message, vk, lparam)

        # Para teclas tipo q, a, s, d, números, etc.
        if is_down and event.text():
            char = event.text()
            if len(char) == 1:
                win32gui.PostMessage(hwnd, win32con.WM_CHAR, ord(char), lparam)

        return True

    except Exception:
        return False


class KeyboardForwarder(QObject):
    def __init__(self, macro_window):
        super().__init__()
        self.macro_window = macro_window

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress:
            if self.macro_window.forward_key_event(event, is_down=True):
                return True

        if event.type() == QEvent.Type.KeyRelease:
            if self.macro_window.forward_key_event(event, is_down=False):
                return True

        return False


# ============================================================
# PANEL EMBEBIDO
# ============================================================

class EmbeddedPanel(QFrame):
    def __init__(self, label):
        super().__init__()

        self.embedded_hwnd = None

        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setStyleSheet("""
            QFrame {
                background-color: #101010;
                border: 2px solid #303030;
                border-radius: 8px;
            }
        """)

        self.placeholder = QLabel(label)
        self.placeholder.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.placeholder.setStyleSheet("""
            QLabel {
                color: #dddddd;
                font-size: 22px;
                font-weight: bold;
                border: none;
            }
        """)

        layout = QVBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.addWidget(self.placeholder)
        self.setLayout(layout)

    def set_embedded_window(self, hwnd):
        if not hwnd:
            return False

        self.embedded_hwnd = hwnd
        self.placeholder.hide()

        embed_window(
            self.embedded_hwnd,
            int(self.winId()),
            self.width(),
            self.height(),
        )

        return True

    def fit_window(self):
        if not self.embedded_hwnd:
            return

        move_embedded_window(
            self.embedded_hwnd,
            self.width(),
            self.height(),
        )

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.fit_window()


# ============================================================
# MACROVENTANA
# ============================================================

class AirDrumsMacroWindow(QMainWindow):
    def __init__(self):
        super().__init__()

        self.setWindowTitle("Panel de Control Principal")
        self.resize(1600, 900)

        self.processes = {
            "middleware": None,
            "game": None,          # Cambiado de "tracking" a "game"
            "unity": None,
            "rhythm": None,
            "voice_listener": None,
            "calibracion": None,   # Nuevo proceso
        }
        self.is_calibrating = False 
        self.active_panel = None
        self.active_panel_name = "ninguno"

        self.opencv_panel = EmbeddedPanel("OPENCV")
        self.unity_panel = EmbeddedPanel("AIRDRUMS / UNITY")
        self.rhythm_panel = EmbeddedPanel("RHYTHM GAME")

        self.status_label = QLabel("Estado: listo")
        self.status_label.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 14px;
                border: none;
            }
        """)

        self.control_opencv_button = QPushButton("Control OpenCV")
        self.control_unity_button = QPushButton("Control Unity")
        self.control_rhythm_button = QPushButton("Control Rhythm")
        self.calibrar_button = QPushButton("Calibrar Batería")

        self.start_button = QPushButton("Iniciar todo")
        self.relayout_button = QPushButton("Reubicar")
        self.stop_button = QPushButton("Cerrar todo")

        self.control_opencv_button.clicked.connect(
            lambda: self.set_active_panel(self.opencv_panel, "OpenCV")
        )
        self.control_unity_button.clicked.connect(
            lambda: self.set_active_panel(self.unity_panel, "Unity")
        )
        self.control_rhythm_button.clicked.connect(
            lambda: self.set_active_panel(self.rhythm_panel, "Rhythm Game")
        )
        self.calibrar_button.clicked.connect(self.start_calibration)
        self.start_button.clicked.connect(self.start_all)
        self.relayout_button.clicked.connect(self.try_embed_windows)
        self.stop_button.clicked.connect(self.stop_all)

        self.build_ui()

        self.embed_timer = QTimer()
        self.embed_timer.timeout.connect(self.try_embed_windows)

        self.fit_timer = QTimer()
        self.fit_timer.timeout.connect(self.fit_all)
        self.fit_timer.start(500)
        self.monitor_timer = QTimer()
        self.monitor_timer.timeout.connect(self.monitor_state)
        self.monitor_timer.start(1000)

        # --- NUEVO: CONFIGURACIÓN UDP PARA ESCUCHAR A RHYTHM_GAME ---
        self.cmd_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.cmd_socket.bind(("127.0.0.1", 5055))
        self.cmd_socket.setblocking(False)

        self.cmd_timer = QTimer()
        self.cmd_timer.timeout.connect(self.poll_commands)
        self.cmd_timer.start(100)  # Revisar cada 100ms

    def poll_commands(self):
        """Lee mensajes UDP no bloqueantes enviados por otros scripts."""
        try:
            while True:
                data, _ = self.cmd_socket.recvfrom(1024)
                command = data.decode("utf-8").strip()
                if command == "START_CALIBRATION":
                    print("¡Mensaje UDP recibido! Iniciando calibración remota...")
                    # Ejecuta sólo si no está ya calibrando
                    if not self.is_calibrating:
                        self.start_calibration()
        except BlockingIOError:
            pass # No hay mensajes nuevos
        except Exception as e:
            print(f"Error de Socket: {e}")
            
    def build_ui(self):
        root = QWidget()
        root.setStyleSheet("background-color: #050505;")

        root_layout = QVBoxLayout()
        root_layout.setContentsMargins(8, 8, 8, 8)
        root_layout.setSpacing(8)

        header = QHBoxLayout()

        title = QLabel("AirDrums")
        title.setStyleSheet("""
            QLabel {
                color: white;
                font-size: 20px;
                font-weight: bold;
                border: none;
            }
        """)

        for button in [
            self.control_unity_button,
            self.control_rhythm_button,
            self.calibrar_button,
            self.start_button,
            self.relayout_button,
            self.stop_button,
        ]:
            button.setFixedHeight(34)
            button.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        header.addWidget(title)
        header.addWidget(self.status_label, stretch=1)

        header.addWidget(self.control_opencv_button)
        header.addWidget(self.control_unity_button)
        header.addWidget(self.control_rhythm_button)
        header.addWidget(self.calibrar_button)
        header.addWidget(self.start_button)
        header.addWidget(self.relayout_button)
        header.addWidget(self.stop_button)

        main_layout = QHBoxLayout()
        main_layout.setSpacing(8)

        left_column = QVBoxLayout()
        left_column.setSpacing(8)

        main_layout.addWidget(self.unity_panel, stretch=1)
        main_layout.addWidget(self.rhythm_panel, stretch=1)

        # Ocultamos explícitamente el panel de OpenCV para que no reaccione en la UI
        self.opencv_panel.hide()

        root_layout.addLayout(header)
        root_layout.addLayout(main_layout, stretch=1)

        root.setLayout(root_layout)
        self.setCentralWidget(root)

    def set_status(self, message):
        self.status_label.setText(f"Estado: {message}")

    def set_active_panel(self, panel, name):
        self.active_panel = panel
        self.active_panel_name = name

        if panel.embedded_hwnd:
            focus_embedded_window(panel.embedded_hwnd)

        self.set_status(f"Control activo: {name}")

    def forward_key_event(self, event, is_down):
        if self.active_panel is None:
            return False

        if self.active_panel.embedded_hwnd is None:
            return False

        return post_key_to_window(
            self.active_panel.embedded_hwnd,
            event,
            is_down
        )

    def launch_python_script(self, script_path):
        if not script_path.exists():
            self.set_status(f"No existe: {script_path.name}")
            return None

        try:
            return subprocess.Popen(
                [PYTHON_EXE, str(script_path)],
                cwd=str(BASE_DIR),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        except Exception as error:
            self.set_status(f"Error lanzando {script_path.name}: {error}")
            return None

    def launch_unity(self, exe_path):
        if not exe_path.exists():
            self.set_status(f"No existe Unity EXE: {exe_path}")
            return None

        try:
            return subprocess.Popen(
                [
                    str(exe_path),
                    "-screen-fullscreen", "0",
                    "-screen-width", "640",
                    "-screen-height", "520",
                ],
                cwd=str(exe_path.parent),
                creationflags=subprocess.CREATE_NEW_PROCESS_GROUP,
            )
        except Exception as error:
            self.set_status(f"Error lanzando Unity: {error}")
            return None

    def start_all(self):
        self.set_status("Iniciando procesos...")

        if self.processes["middleware"] is None:
            self.processes["middleware"] = self.launch_python_script(MIDDLEWARE_SCRIPT)
            time.sleep(0.5)

        if self.processes["game"] is None:
            self.processes["game"] = self.launch_python_script(GAME_SCRIPT)
            time.sleep(1.0)

        if self.processes["unity"] is None:
            self.processes["unity"] = self.launch_unity(UNITY_EXE)
            time.sleep(1.0)

        if self.processes["rhythm"] is None:
            self.processes["rhythm"] = self.launch_python_script(RHYTHM_SCRIPT)
            time.sleep(1.0)

        if self.processes["voice_listener"] is None:
            self.processes["voice_listener"] = self.launch_python_script(VOICE_LISTENER_SCRIPT)
            time.sleep(0.5)

        self.set_status("Procesos iniciados. Buscando ventanas...")
        self.embed_timer.start(700)

    def kill_process(self, name):
        """Cierra un proceso específico limpiamente."""
        process = self.processes.get(name)
        if process is not None and process.poll() is None:
            try:
                process.terminate()
                process.wait(timeout=2)
            except Exception:
                try:
                    process.kill()
                except Exception:
                    pass
        self.processes[name] = None

    def start_calibration(self):
        """Mata Unity y Game, e inicia la calibración inyectándola en el panel."""
        self.set_status("Iniciando calibración...")
        self.is_calibrating = True
        
        self.kill_process("unity")
        self.kill_process("game")
        
        # Preparar el panel de Unity para recibir OpenCV
        self.unity_panel.embedded_hwnd = None
        self.unity_panel.placeholder.setText("CALIBRANDO...")
        self.unity_panel.placeholder.show()

        # Lanzar calibración
        self.processes["calibracion"] = self.launch_python_script(CALIBRACION_SCRIPT)
        
        self.embed_timer.start(700) # Enciende el emparejador de ventanas

    def monitor_state(self):
        """Revisa constantemente si el script de calibración se cerró solo."""
        if self.is_calibrating:
            cal_proc = self.processes.get("calibracion")
            # Si el proceso existe pero poll() ya no es None, significa que terminó
            if cal_proc and cal_proc.poll() is not None:
                self.set_status("Calibración terminada. Reiniciando entorno...")
                self.is_calibrating = False
                self.processes["calibracion"] = None
                
                # Desvincular ventana y preparar panel
                self.unity_panel.embedded_hwnd = None
                self.unity_panel.placeholder.setText("AIRDRUMS / UNITY")
                self.unity_panel.placeholder.show()
                
                # Volver a lanzar Unity y el Tracker 
                self.processes["game"] = self.launch_python_script(GAME_SCRIPT)
                time.sleep(1.0)
                self.processes["unity"] = self.launch_unity(UNITY_EXE)
                
                self.embed_timer.start(700)

    def try_embed_windows(self):
        changed = False

        rhythm_process = self.processes.get("rhythm")

        # --- Lógica dinámica del Panel Central (Unity o Calibración) ---
        if self.is_calibrating:
            cal_process = self.processes.get("calibracion")
            if self.unity_panel.embedded_hwnd is None:
                hwnd = find_window_for_process_or_global(cal_process, OPENCV_TITLE_HINTS)
                if hwnd:
                    self.unity_panel.set_embedded_window(hwnd)
                    changed = True
        else:
            unity_process = self.processes.get("unity")
            if self.unity_panel.embedded_hwnd is None:
                hwnd = find_window_for_process_or_global(unity_process, UNITY_TITLE_HINTS)
                if hwnd:
                    self.unity_panel.set_embedded_window(hwnd)
                    changed = True

        # --- Rhythm Game ---
        if self.rhythm_panel.embedded_hwnd is None:
            hwnd = find_window_for_process_or_global(rhythm_process, RHYTHM_TITLE_HINTS)
            if hwnd:
                self.rhythm_panel.set_embedded_window(hwnd)
                changed = True

        # --- Ocultar ventanas auxiliares ---
        hide_window_by_hints(MASK_TITLE_HINTS) # Oculta la máscara
        if not self.is_calibrating:
            # Si NO estamos calibrando, el juego general arrojará una ventana en game.py que no queremos ver
            hide_window_by_hints(OPENCV_TITLE_HINTS) 

        self.fit_all()

        all_ready = (
            self.opencv_panel.embedded_hwnd is not None
            and self.unity_panel.embedded_hwnd is not None
            and self.rhythm_panel.embedded_hwnd is not None
        )

        if all_ready:
            if self.active_panel is None:
                self.set_active_panel(self.rhythm_panel, "Rhythm Game")

            self.set_status(
                f"Todo encajado. Control activo: {self.active_panel_name}"
            )
            self.embed_timer.stop()
        elif changed:
            self.set_status("Algunas ventanas encajadas. Esperando las demás...")
        else:
            self.set_status("Buscando ventanas externas...")

    def fit_all(self):
        self.opencv_panel.fit_window()
        self.unity_panel.fit_window()
        self.rhythm_panel.fit_window()

    def stop_all(self):
        self.embed_timer.stop()

        for name, process in self.processes.items():
            if process is None:
                continue

            try:
                process.terminate()
            except Exception:
                pass

            self.processes[name] = None

        self.opencv_panel.embedded_hwnd = None
        self.unity_panel.embedded_hwnd = None
        self.rhythm_panel.embedded_hwnd = None

        self.opencv_panel.placeholder.show()
        self.unity_panel.placeholder.show()
        self.rhythm_panel.placeholder.show()

        self.active_panel = None
        self.active_panel_name = "ninguno"

        self.set_status("Procesos cerrados.")

    def closeEvent(self, event):
        self.stop_all()
        event.accept()


def main():
    app = QApplication(sys.argv)

    window = AirDrumsMacroWindow()

    keyboard_forwarder = KeyboardForwarder(window)
    app.installEventFilter(keyboard_forwarder)
    window.keyboard_forwarder = keyboard_forwarder

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()