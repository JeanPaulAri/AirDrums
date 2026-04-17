import json
import socket
import threading
import time


LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 5051
UNITY_HOST = "127.0.0.1"
UNITY_PORT = 5052
GAME_UI_HOST = "127.0.0.1"
GAME_UI_PORT = 5053
BUFFER_SIZE = 65535


PAD_CODE_TO_ZONE = {
    "(0,0)": "platillo",
    "(0,1)": "tom superior",
    "(1,0)": "hithat",
    "(1,1)": "tarola",
    "(2,1)": "bombo",
    "(2,2)": "tom inferior",
}


class AirDrumsMiddleware:
    # Inicializa sockets, estado compartido y estructuras necesarias para procesar mensajes en tiempo real.
    def __init__(self):
        self.drum_zones = {}
        self.frame_size = {"dim_x": None, "dim_y": None}
        self.state_lock = threading.Lock()
        self.running = False
        self.listener_thread = None

        self.listen_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listen_socket.bind((LISTEN_HOST, LISTEN_PORT))

        self.unity_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    # Inicia el hilo de escucha para recibir mensajes sin bloquear la ejecucion principal.
    def start(self):
        if self.running:
            return

        self.running = True
        self.listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.listener_thread.start()
        print(
            "[INFO] Middleware activo. Escuchando AirDrums en UDP "
            f"{LISTEN_HOST}:{LISTEN_PORT}, Unity en {UNITY_HOST}:{UNITY_PORT} "
            f"y Game UI en {GAME_UI_HOST}:{GAME_UI_PORT}"
        )

    # Detiene el middleware y cierra los sockets abiertos.
    def stop(self):
        self.running = False

        try:
            self.listen_socket.close()
        except OSError:
            pass

        try:
            self.unity_socket.close()
        except OSError:
            pass

    # Espera paquetes UDP y entrega cada mensaje al flujo adecuado.
    def _listen_loop(self):
        while self.running:
            try:
                data, address = self.listen_socket.recvfrom(BUFFER_SIZE)
            except OSError:
                break

            self._handle_packet(data, address)

    # Decodifica el JSON y envia cada mensaje al manejador que corresponda.
    def _handle_packet(self, data, address):
        try:
            message = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            print(f"[WARN] Paquete invalido desde {address}: {error}")
            return

        message_type = message.get("tipo")

        if message_type == "configuracion":
            self._handle_configuration(message)
            return

        if message_type == "posicion":
            self._handle_position(message)
            return

        if message_type == "golpe":
            self._handle_hit(message)
            return

        print(f"[WARN] Tipo de mensaje desconocido: {message_type}")

    # Guarda la configuracion inicial de la bateria enviada desde el sistema de vision.
    def _handle_configuration(self, message):
        zones = self._parse_configuration(message)
        if not zones:
            print("[WARN] Configuracion recibida, pero no se pudieron extraer zonas validas")
            return

        with self.state_lock:
            self.drum_zones = zones
            self.frame_size["dim_x"] = message.get("dim_x")
            self.frame_size["dim_y"] = message.get("dim_y")

        unity_payload = {
            "tipo": "configuracion",
            "dim_x": message.get("dim_x"),
            "dim_y": message.get("dim_y"),
            "elementos": list(zones.values()),
        }
        self._broadcast(unity_payload)
        print(f"[INFO] Configuracion cargada con zonas: {', '.join(zones.keys())}")

    # Reenvia las posiciones al resto de clientes para depuracion y visualizacion.
    def _handle_position(self, message):
        unity_payload = {
            "tipo": "posicion",
            "stick_1_x": message.get("stick_1_x"),
            "stick_1_y": message.get("stick_1_y"),
            "stick_2_x": message.get("stick_2_x"),
            "stick_2_y": message.get("stick_2_y"),
            "stick_3_x": message.get("stick_3_x"),
            "stick_3_y": message.get("stick_3_y"),
        }
        self._broadcast(unity_payload)

    # Convierte un golpe del sistema de vision en el mensaje minimo de gameplay.
    def _handle_hit(self, message):
        with self.state_lock:
            zones_ready = bool(self.drum_zones)

        if not zones_ready:
            print("[WARN] Golpe ignorado porque aun no hay configuracion")
            return

        pad_code = self._normalize_pad_code(message.get("pad"))
        zone = PAD_CODE_TO_ZONE.get(pad_code)

        if zone is None:
            print(f"[WARN] Pad desconocido en golpe: {message.get('pad')}")
            return

        payload = {"zone": zone}
        self._broadcast(payload)

    # Convierte la lista de elementos calibrados en un diccionario simple de zonas y coordenadas.
    def _parse_configuration(self, message):
        elements = message.get("elementos")
        normalized = {}

        if not isinstance(elements, list):
            return normalized

        for element in elements:
            if not isinstance(element, dict):
                continue

            pad_code = self._normalize_pad_code(element.get("elemento"))
            zone = PAD_CODE_TO_ZONE.get(pad_code)
            if zone is None:
                continue

            x = element.get("x")
            y = element.get("y")
            if x is None or y is None:
                continue

            normalized[zone] = {
                "zone": zone,
                "pad": pad_code,
                "x": x,
                "y": y,
            }

        return normalized

    # Limpia el formato de las tuplas para aceptar espacios como "(1, 2)" o "(1,2)".
    def _normalize_pad_code(self, raw_code):
        if raw_code is None:
            return None

        return str(raw_code).strip().replace(" ", "")

    # Reenvia a los consumidores el mensaje que corresponda.
    def _broadcast(self, payload):
        try:
            encoded = json.dumps(payload).encode("utf-8")
            for host, port in (
                (UNITY_HOST, UNITY_PORT),
                (GAME_UI_HOST, GAME_UI_PORT),
            ):
                self.unity_socket.sendto(encoded, (host, port))
            print(f"[SEND] {payload}")
        except OSError as error:
            print(f"[WARN] No se pudo enviar a un cliente: {error}")


# Levanta el middleware y lo mantiene activo hasta que el usuario lo detenga manualmente.
def main():
    middleware = AirDrumsMiddleware()
    middleware.start()

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[INFO] Cerrando middleware...")
        middleware.stop()


if __name__ == "__main__":
    main()
