import json
import socket
import threading
import time


LISTEN_HOST = "0.0.0.0"
LISTEN_PORT = 5051
UNITY_HOST = "127.0.0.1"
UNITY_PORT = 5052
BUFFER_SIZE = 65535


ZONE_ALIASES = {
    "tarola": "snare",
    "hithat": "hihat",
    "platillo": "crash",
    "tom": "tom",
    "tom_grave": "floor_tom",
    "bombo": "kick",
}


class AirDrumsMiddleware:
    def __init__(self):
        self.drum_zones = {}
        self.zones_lock = threading.Lock()
        self.running = False
        self.listener_thread = None

        self.listen_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listen_socket.bind((LISTEN_HOST, LISTEN_PORT))

        self.unity_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

    def start(self):
        if self.running:
            return

        self.running = True
        self.listener_thread = threading.Thread(target=self._listen_loop, daemon=True)
        self.listener_thread.start()
        print(
            "[INFO] Middleware activo. Escuchando OpenCV en UDP "
            f"{LISTEN_HOST}:{LISTEN_PORT} y reenviando a Unity en {UNITY_HOST}:{UNITY_PORT}"
        )

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

    def _listen_loop(self):
        while self.running:
            try:
                data, address = self.listen_socket.recvfrom(BUFFER_SIZE)
                received_at = time.time()
            except OSError:
                break

            self._handle_packet(data, address, received_at)

    def _handle_packet(self, data, address, received_at):
        try:
            message = json.loads(data.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            print(f"[WARN] Paquete invalido desde {address}: {error}")
            return

        message_type = message.get("type")

        if message_type == "calibration":
            self._handle_calibration(message)
            return

        if message_type == "hit":
            self._handle_hit(message, received_at)
            return

        print(f"[WARN] Tipo de mensaje desconocido: {message_type}")

    def _handle_calibration(self, message):
        zones = self._parse_calibration(message)
        if not zones:
            print("[WARN] Calibration recibida, pero no se pudieron extraer zonas validas")
            return

        with self.zones_lock:
            self.drum_zones = zones

        print(f"[INFO] Calibration cargada con zonas: {', '.join(zones.keys())}")

    def _handle_hit(self, message, received_at):
        if not message.get("hit"):
            return

        with self.zones_lock:
            zones_ready = bool(self.drum_zones)
            current_zones = dict(self.drum_zones)

        if not zones_ready:
            print("[WARN] Hit ignorado porque aun no hay calibration")
            return

        source = message.get("source")
        x = message.get("x")
        y = message.get("y")

        if source == "kick_pedal":
            zone = "kick"
            stick = "foot"
        elif source == "stick_right":
            zone = self._detect_zone(current_zones, x, y)
            stick = "right"
        elif source == "stick_left":
            zone = self._detect_zone(current_zones, x, y)
            stick = "left"
        else:
            print(f"[WARN] Fuente de hit desconocida: {source}")
            return

        if zone is None:
            print(f"[WARN] No se encontro zona para hit en ({x}, {y}) desde {source}")
            return

        payload = {
            "zone": zone,
            "stick": stick,
            "timestamp": received_at,
        }
        self._send_to_unity(payload)

    def _send_to_unity(self, payload):
        try:
            encoded = json.dumps(payload).encode("utf-8")
            self.unity_socket.sendto(encoded, (UNITY_HOST, UNITY_PORT))
            print(f"[SEND] {payload}")
        except OSError as error:
            print(f"[WARN] No se pudo enviar a Unity: {error}")

    def _parse_calibration(self, message):
        raw_zones = (
            message.get("zones")
            or message.get("drum_zones")
            or message.get("calibration")
            or message.get("data")
        )
        normalized = {}

        if isinstance(raw_zones, dict):
            for zone_name, zone_data in raw_zones.items():
                zone = self._normalize_zone(zone_name, zone_data)
                if zone is not None:
                    normalized[zone["name"]] = zone
        elif isinstance(raw_zones, list):
            for zone_data in raw_zones:
                zone = self._normalize_zone(None, zone_data)
                if zone is not None:
                    normalized[zone["name"]] = zone

        return normalized

    def _normalize_zone(self, fallback_name, zone_data):
        if not isinstance(zone_data, dict):
            return None

        raw_name = (
            zone_data.get("name")
            or zone_data.get("zone")
            or zone_data.get("label")
            or fallback_name
        )
        name = self._canonical_zone_name(raw_name)
        if name is None:
            return None

        if "center" in zone_data and isinstance(zone_data["center"], (list, tuple)) and len(zone_data["center"]) >= 2:
            center_x = zone_data["center"][0]
            center_y = zone_data["center"][1]
            if "radius" in zone_data:
                return {
                    "name": name,
                    "shape": "circle",
                    "x": center_x,
                    "y": center_y,
                    "radius": zone_data["radius"],
                }

        if "center" in zone_data and isinstance(zone_data["center"], dict):
            center_x = zone_data["center"].get("x")
            center_y = zone_data["center"].get("y")
            if "radius" in zone_data:
                return {
                    "name": name,
                    "shape": "circle",
                    "x": center_x,
                    "y": center_y,
                    "radius": zone_data["radius"],
                }

        if all(key in zone_data for key in ("x", "y", "radius")):
            return {
                "name": name,
                "shape": "circle",
                "x": zone_data["x"],
                "y": zone_data["y"],
                "radius": zone_data["radius"],
            }

        if all(key in zone_data for key in ("cx", "cy", "rx", "ry")):
            return {
                "name": name,
                "shape": "ellipse",
                "cx": zone_data["cx"],
                "cy": zone_data["cy"],
                "rx": zone_data["rx"],
                "ry": zone_data["ry"],
            }

        if all(key in zone_data for key in ("x", "y", "width", "height")):
            return {
                "name": name,
                "shape": "rect",
                "x": zone_data["x"],
                "y": zone_data["y"],
                "width": zone_data["width"],
                "height": zone_data["height"],
            }

        if all(key in zone_data for key in ("left", "top", "right", "bottom")):
            return {
                "name": name,
                "shape": "rect",
                "x": zone_data["left"],
                "y": zone_data["top"],
                "width": zone_data["right"] - zone_data["left"],
                "height": zone_data["bottom"] - zone_data["top"],
            }

        print(f"[WARN] Zona '{name}' sin geometria compatible: {zone_data}")
        return None

    def _canonical_zone_name(self, raw_name):
        if not raw_name:
            return None

        cleaned = str(raw_name).strip().lower().replace("-", "_")
        cleaned = " ".join(cleaned.split())
        return ZONE_ALIASES.get(cleaned, cleaned.replace(" ", "_"))

    def _detect_zone(self, zones, x, y):
        if x is None or y is None:
            return None

        for zone_name, zone in zones.items():
            if zone_name == "kick":
                continue

            shape = zone.get("shape")

            if shape == "circle" and self._point_in_circle(x, y, zone):
                return zone_name

            if shape == "ellipse" and self._point_in_ellipse(x, y, zone):
                return zone_name

            if shape == "rect" and self._point_in_rect(x, y, zone):
                return zone_name

        return None

    def _point_in_circle(self, x, y, zone):
        dx = x - zone["x"]
        dy = y - zone["y"]
        radius = zone["radius"]
        return (dx * dx) + (dy * dy) <= (radius * radius)

    def _point_in_ellipse(self, x, y, zone):
        rx = zone["rx"]
        ry = zone["ry"]
        if rx == 0 or ry == 0:
            return False

        dx = x - zone["cx"]
        dy = y - zone["cy"]
        return ((dx * dx) / (rx * rx)) + ((dy * dy) / (ry * ry)) <= 1

    def _point_in_rect(self, x, y, zone):
        return (
            zone["x"] <= x <= zone["x"] + zone["width"]
            and zone["y"] <= y <= zone["y"] + zone["height"]
        )


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

