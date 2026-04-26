"""Juego rítmico de batería (Drum Hero) con lectura de charts MIDI y playback de stems.

Este módulo carga canciones desde assets/songs, interpreta PART DRUMS del MIDI
y sincroniza audio (song/vocals/rhythm/drums_1..4) con el gameplay.
"""

import configparser
import concurrent.futures
import importlib.util
import json
import math
import socket
import subprocess
import sys
import unicodedata
from collections import deque
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path
import os
import pygame

# Tamaño de ventana y rendimiento.
WINDOW_WIDTH = 1280
WINDOW_HEIGHT = 720
FPS = 60

# Entrada de golpes desde middleware (OpenCV -> UDP -> juego).
UDP_HOST = "127.0.0.1"
UDP_PORT = 5053
UDP_BUFFER_SIZE = 2048

# Título y carpeta base de canciones.
APP_TITLE = "AirDrums Rhythm Highway"
SONGS_DIR = Path(__file__).resolve().parent / "assets" / "songs"
TRACKING_SCRIPT_PATH = Path(__file__).resolve().parent / "main.py"
VOICE_LISTENER_SCRIPT_PATH = Path(__file__).resolve().parent / "voice_listener.ps1"
VOICE_COMMAND_PATH = Path(__file__).resolve().parent / "_voice_command.json"
CREDIT_LINES = [
    "AirDrums",
    "Proyecto universitario de interaccion humano-computador",
    "Equipo creador: actualiza estos nombres en team dinamita",
]

# Paleta de colores UI.
BACKGROUND_TOP = (16, 12, 22)
BACKGROUND_BOTTOM = (62, 26, 16)
HIGHWAY_EDGE = (226, 226, 226)
HIGHWAY_FILL = (32, 25, 24)
HUD_TEXT = (241, 232, 208)
MISS_TEXT = (255, 117, 117)
# Ventanas de timing y arranque.
EARLY_HIT_WINDOW_SECONDS = 0.120
LATE_HIT_WINDOW_SECONDS = 0.120
PREVIEW_LEAD_SECONDS = 3.35
START_DELAY_SECONDS = 1.5
# Filtros para reducir densidad en charts de batería.
DRUM_FRIENDLY_NOTE_GAP_SECONDS = 0.26
DRUM_FRIENDLY_KICK_GAP_SECONDS = 0.50
DRUM_CHORD_WINDOW_SECONDS = 0.14
DRUM_GLOBAL_MIN_GAP_SECONDS = 0.22
DRUM_MAX_NOTES_PER_SECOND = 4
# Curva de viaje de notas (sensación de velocidad en pantalla).
NOTE_TRAVEL_CURVE = 1.45
ADAPT_CLUSTER_WINDOW_SECONDS = 0.15

GLOBAL_OFFSET_SECONDS = 0

# Colores de los carriles (platillo -> tom inferior).
LANE_COLORS = [
    (61, 203, 90),
    (214, 49, 47),
    (242, 211, 54),
    (70, 165, 247),
    (255, 157, 45),
]
# Color del bombo.
KICK_COLOR = (177, 80, 255)
KICK_COLOR_PRESSED = (212, 160, 255)

# Mapeo de zona de batería a carril visual.
ZONE_TO_LANE = {
    "platillo": 0,
    "hithat": 1,
    "tarola": 2,
    "tom superior": 3,
    "tom inferior": 4,
}

# Etiquetas visibles por zona.
ZONE_LABELS = {
    "platillo": "PLATILLO",
    "hithat": "HI-HAT",
    "tarola": "TAROLA",
    "tom superior": "TOM SUPERIOR",
    "tom inferior": "TOM INFERIOR",
    "bombo": "BOMBO",
}

# Teclas para pruebas manuales en PC.
KEYBOARD_ZONE_MAP = {
    pygame.K_a: "platillo",
    pygame.K_s: "hithat",
    pygame.K_d: "tarola",
    pygame.K_j: "tom superior",
    pygame.K_k: "tom inferior",
    pygame.K_SPACE: "bombo",
}

# Mapas de notas MIDI (expert) a zonas.
GUITAR_EXPERT_MAP = {
    96: "platillo",
    97: "hithat",
    98: "tarola",
    99: "tom superior",
    100: "tom inferior",
}

DRUM_EXPERT_MAP = {
    96: "bombo",
    97: "tarola",
    98: "hithat",
    99: "tom superior",
    100: "platillo",
    101: "tom inferior",
}


@dataclass
class Note:
    """Nota de gameplay: tiempo en segundos y zona de batería a golpear."""
    time: float
    zone: str
    hit: bool = False
    judged: bool = False


@dataclass
class SongData:
    """Metadatos y assets de una canción cargada."""
    title: str
    artist: str
    album: str
    charter: str
    audio_path: Path | None
    cover_path: Path | None
    vocals_path: Path | None
    rhythm_path: Path | None
    drums_paths: list[Path]
    base_notes: list[Note]
    notes: list[Note]
    length_seconds: float
    source_name: str
    inferred_kick: bool


@dataclass(frozen=True)
class DifficultyProfile:
    """Parámetros de simplificación y supervivencia por dificultad."""
    key: str
    label: str
    note_gap_seconds: float
    kick_gap_seconds: float
    chord_window_seconds: float
    global_min_gap_seconds: float
    max_notes_per_second: int
    health_gain_hit: float
    health_loss_miss: float
    health_loss_bad_hit: float
    fail_streak: int
    gap_fill_threshold_seconds: float
    gap_fill_max_notes: int
    fill_zones: tuple[str, ...]
    max_kicks_in_window: int
    kick_density_window_seconds: float


DIFFICULTY_ORDER = ["easy", "medium", "hard"]
DIFFICULTY_PROFILES = {
    "easy": DifficultyProfile(
        key="easy",
        label="Facil",
        note_gap_seconds=DRUM_FRIENDLY_NOTE_GAP_SECONDS,
        kick_gap_seconds=DRUM_FRIENDLY_KICK_GAP_SECONDS,
        chord_window_seconds=DRUM_CHORD_WINDOW_SECONDS,
        global_min_gap_seconds=DRUM_GLOBAL_MIN_GAP_SECONDS,
        max_notes_per_second=DRUM_MAX_NOTES_PER_SECOND,
        health_gain_hit=0.028,
        health_loss_miss=0.060,
        health_loss_bad_hit=0.028,
        fail_streak=14,
        gap_fill_threshold_seconds=99.0,
        gap_fill_max_notes=0,
        fill_zones=(),
        max_kicks_in_window=2,
        kick_density_window_seconds=1.35,
    ),
    "medium": DifficultyProfile(
        key="medium",
        label="Medio",
        note_gap_seconds=0.19,
        kick_gap_seconds=0.36,
        chord_window_seconds=0.10,
        global_min_gap_seconds=0.14,
        max_notes_per_second=6,
        health_gain_hit=0.022,
        health_loss_miss=0.045,
        health_loss_bad_hit=0.022,
        fail_streak=16,
        gap_fill_threshold_seconds=0.72,
        gap_fill_max_notes=1,
        fill_zones=("hithat", "tom superior", "hithat", "tom inferior"),
        max_kicks_in_window=2,
        kick_density_window_seconds=1.1,
    ),
    "hard": DifficultyProfile(
        key="hard",
        label="Dificil",
        note_gap_seconds=0.11,
        kick_gap_seconds=0.24,
        chord_window_seconds=0.05,
        global_min_gap_seconds=0.07,
        max_notes_per_second=9,
        health_gain_hit=0.020,
        health_loss_miss=0.055,
        health_loss_bad_hit=0.026,
        fail_streak=14,
        gap_fill_threshold_seconds=0.42,
        gap_fill_max_notes=2,
        fill_zones=("hithat", "tom superior", "tom inferior", "platillo", "tom superior", "tom inferior"),
        max_kicks_in_window=3,
        kick_density_window_seconds=1.0,
    ),
}


class VoiceCommandListener:
    """Escucha comandos de voz con libreria Python y usa Windows como respaldo."""
    def __init__(self, script_path: Path, output_path: Path):
        self.script_path = script_path
        self.output_path = output_path
        self.process = None
        self.last_timestamp = None
        self.available = False
        self.status_message = "Voz no inicializada"
        self.backend_name = "Ninguno"
        self.pending_commands = deque()
        self.pending_audio = deque(maxlen=1)
        self.stop_listening = None
        self.recognizer = None
        self.microphone = None
        self.executor = None
        self.recognition_future = None
        self.last_file_mtime = 0.0

    def start(self):
        if self._start_python_microphone():
            return
        self._start_windows_fallback()

    def _start_python_microphone(self):
        if importlib.util.find_spec("speech_recognition") is None or importlib.util.find_spec("pyaudio") is None:
            return False

        try:
            import speech_recognition as sr

            self.recognizer = sr.Recognizer()
            self.recognizer.dynamic_energy_threshold = True
            self.recognizer.energy_threshold = 250
            self.recognizer.pause_threshold = 0.35
            self.recognizer.non_speaking_duration = 0.18
            self.recognizer.operation_timeout = 2.0
            self.microphone = sr.Microphone()
            self.executor = concurrent.futures.ThreadPoolExecutor(max_workers=1)
            with self.microphone as source:
                self.recognizer.adjust_for_ambient_noise(source, duration=0.5)
            self.stop_listening = self.recognizer.listen_in_background(
                self.microphone,
                self._python_callback,
                phrase_time_limit=1.5,
            )
            self.available = True
            self.backend_name = "Microfono Python"
            self.status_message = "Voz lista con SpeechRecognition"
            return True
        except Exception as exc:
            self.available = False
            self.backend_name = "Ninguno"
            self.status_message = f"No pude iniciar SpeechRecognition: {exc}"
            return False

    def _python_callback(self, recognizer, audio):
        if self.pending_audio.maxlen and len(self.pending_audio) >= self.pending_audio.maxlen:
            self.pending_audio.clear()
        self.pending_audio.append(audio)

    def _recognize_audio(self, audio):
        recognized_text = None
        try:
            recognized_text = self.recognizer.recognize_google(audio, language="es-ES")
        except Exception:
            try:
                recognized_text = self.recognizer.recognize_google(audio, language="es-MX")
            except Exception:
                try:
                    recognized_text = self.recognizer.recognize_sphinx(audio)
                except Exception:
                    recognized_text = None
        return recognized_text

    def _poll_python_command(self):
        if self.recognition_future is not None and self.recognition_future.done():
            try:
                recognized_text = self.recognition_future.result()
            except Exception:
                recognized_text = None
            self.recognition_future = None
            if recognized_text:
                self.pending_commands.append(recognized_text)

        if self.recognition_future is None and self.pending_audio and self.executor is not None:
            next_audio = self.pending_audio.popleft()
            self.recognition_future = self.executor.submit(self._recognize_audio, next_audio)

    def _start_windows_fallback(self):
        if not self.script_path.exists():
            self.status_message = "No encontre el listener de voz"
            self.backend_name = "Ninguno"
            return

        try:
            if self.output_path.exists():
                self.output_path.unlink()
        except OSError:
            pass

        command = [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(self.script_path),
            "-OutputPath",
            str(self.output_path),
        ]
        try:
            self.process = subprocess.Popen(
                command,
                cwd=str(self.script_path.parent),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            self.available = True
            self.backend_name = "Microfono Windows"
            self.status_message = "Voz lista"
        except OSError:
            self.process = None
            self.available = False
            self.backend_name = "Ninguno"
            self.status_message = "No pude iniciar voz de Windows"

    def poll_command(self):
        if self.backend_name == "Microfono Python":
            self._poll_python_command()
        if self.pending_commands:
            return self.pending_commands.popleft()
        if self.process is not None and self.process.poll() is not None:
            self.available = False
            self.status_message = "El listener de voz se cerro"
            return None
        if not self.available or not self.output_path.exists():
            return None

        # --- NUEVO CÓDIGO (Optimización de I/O de disco) ---
        try:
            # Obtener el tiempo de modificación del archivo sin abrirlo
            current_mtime = os.stat(self.output_path).st_mtime
            
            # Si el archivo no ha sido modificado desde la última vez que lo leímos, salimos temprano
            if current_mtime == self.last_file_mtime:
                return None
        except OSError:
            return None
            
        # Si llegamos aquí, el archivo fue modificado recientemente, así que podemos leerlo de forma segura
        # ---------------------------------------------------

        try:
            payload = json.loads(self.output_path.read_text(encoding="utf-8-sig"))
            # --- NUEVO CÓDIGO ---
            # Guardamos el tiempo de modificación después de una lectura exitosa
            self.last_file_mtime = current_mtime
            # --------------------
        except (OSError, json.JSONDecodeError):
            return None

        timestamp = payload.get("timestamp")
        if not timestamp or timestamp == self.last_timestamp:
            return None

        self.last_timestamp = timestamp
        return payload.get("command")

    def stop(self):
        if self.stop_listening is not None:
            try:
                self.stop_listening(wait_for_stop=False)
            except Exception:
                pass
        self.stop_listening = None
        if self.executor is not None:
            try:
                self.executor.shutdown(wait=False, cancel_futures=True)
            except Exception:
                pass
        self.executor = None
        self.recognition_future = None
        if self.process is not None:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                pass
        self.process = None
        try:
            if self.output_path.exists():
                self.output_path.unlink()
        except OSError:
            pass


class UdpHitReceiver:
    """Recibe golpes de batería desde middleware por UDP (no bloqueante)."""
    def __init__(self, host: str, port: int):
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind((host, port))
        self.socket.setblocking(False)

    def poll_hits(self):
        hits = []

        while True:
            try:
                data, _ = self.socket.recvfrom(UDP_BUFFER_SIZE)
            except BlockingIOError:
                break

            try:
                message = json.loads(data.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue

            zone = message.get("zone")
            if zone:
                hits.append(zone)

        return hits

    def close(self):
        self.socket.close()


class SongLoader:
    """Carga canciones desde disco y extrae charts PART DRUMS del MIDI."""
    def load_all_songs(self):
        """Devuelve la lista de canciones válidas o un demo si no hay songs."""
        songs = []

        if not SONGS_DIR.exists():
            return [self._build_demo_song()]

        song_folders = sorted(path for path in SONGS_DIR.iterdir() if path.is_dir())
        for folder in song_folders:
            song = self._load_song_folder(folder)
            if song is not None:
                songs.append(song)

        if songs:
            return songs

        return [self._build_demo_song()]

    def _load_song_folder(self, folder: Path):
        """Lee metadata, paths de audio y notes.mid; valida PART DRUMS."""
        song_ini_path = folder / "song.ini"
        midi_path = folder / "notes.mid"
        audio_path = folder / "song.ogg"
        cover_path = folder / "album.png"
        vocals_path = folder / "vocals.ogg"
        rhythm_path = folder / "rhythm.ogg"
        drums_paths = []
        for index in range(1, 5):
            drum_file = folder / f"drums_{index}.ogg"
            if drum_file.exists():
                drums_paths.append(drum_file)

        if not song_ini_path.exists() or not midi_path.exists():
            return None

        metadata = self._read_song_ini(song_ini_path)
        base_notes, chart_length, source_name, inferred_kick = self._read_chart(midi_path)

        if not base_notes:
            return None

        easy_notes = self.build_playable_notes(base_notes, "easy")

        return SongData(
            title=metadata.get("name", folder.name),
            artist=metadata.get("artist", "Desconocido"),
            album=metadata.get("album", ""),
            charter=metadata.get("charter", ""),
            audio_path=audio_path if audio_path.exists() else None,
            cover_path=cover_path if cover_path.exists() else None,
            vocals_path=vocals_path if vocals_path.exists() else None,
            rhythm_path=rhythm_path if rhythm_path.exists() else None,
            drums_paths=drums_paths,
            base_notes=self._clone_notes(base_notes),
            notes=easy_notes,
            length_seconds=max(chart_length, float(metadata.get("song_length", "0")) / 1000.0),
            source_name=source_name,
            inferred_kick=inferred_kick,
        )

    def _read_song_ini(self, song_ini_path: Path):
        """Lee el song.ini (sección [song]) como dict."""
        config = configparser.ConfigParser(interpolation=None, strict=False)
        config.optionxform = str
        config.read(song_ini_path, encoding="utf-8")
        return dict(config["song"]) if config.has_section("song") else {}

    def _read_chart(self, midi_path: Path):
        """Lee el MIDI y devuelve notas PART DRUMS en segundos."""
        midi_data = midi_path.read_bytes()
        if midi_data[:4] != b"MThd":
            return [], 0.0, "Desconocido", False

        header_length = int.from_bytes(midi_data[4:8], "big")
        track_count = int.from_bytes(midi_data[10:12], "big")
        division = int.from_bytes(midi_data[12:14], "big")

        track_position = 8 + header_length
        tempo_events = [(0, 500000)]
        track_notes = {}

        for _ in range(track_count):
            if midi_data[track_position:track_position + 4] != b"MTrk":
                return [], 0.0, "Desconocido", False

            track_length = int.from_bytes(midi_data[track_position + 4:track_position + 8], "big")
            track_bytes = midi_data[track_position + 8:track_position + 8 + track_length]
            track_position += 8 + track_length

            track_name, notes, tempos = self._parse_track(track_bytes)
            if track_name:
                track_notes[track_name] = notes
            tempo_events.extend(tempos)

        converter = MidiTempoConverter(division, tempo_events)

        if "PART DRUMS" in track_notes:
            notes = self._build_notes_from_track(track_notes["PART DRUMS"], converter, DRUM_EXPERT_MAP)
            notes = self._cleanup_real_drum_chart(notes)
            return notes, self._song_length_seconds(notes), "PART DRUMS", False

        # Only accept true drum charts for Drum Hero mode
        return [], 0.0, "Sin PART DRUMS", False

    def _parse_track(self, track_bytes: bytes):
        """Parsea un track MIDI y devuelve (nombre, notas, tempos)."""
        position = 0
        running_status = None
        absolute_tick = 0
        track_name = None
        notes = []
        tempo_events = []

        while position < len(track_bytes):
            delta, position = self._read_variable_length(track_bytes, position)
            absolute_tick += delta

            status = track_bytes[position]
            if status < 0x80:
                status = running_status
            else:
                position += 1
                running_status = status

            if status == 0xFF:
                meta_type = track_bytes[position]
                position += 1
                meta_length, position = self._read_variable_length(track_bytes, position)
                meta_data = track_bytes[position:position + meta_length]
                position += meta_length

                if meta_type == 0x03:
                    track_name = meta_data.decode("latin1", "replace")
                elif meta_type == 0x51 and len(meta_data) == 3:
                    tempo = int.from_bytes(meta_data, "big")
                    tempo_events.append((absolute_tick, tempo))

                continue

            if status in (0xF0, 0xF7):
                event_length, position = self._read_variable_length(track_bytes, position)
                position += event_length
                continue

            event_type = status & 0xF0
            if event_type in (0x80, 0x90, 0xA0, 0xB0, 0xE0):
                note_number = track_bytes[position]
                velocity = track_bytes[position + 1]
                position += 2
                if event_type == 0x90 and velocity > 0:
                    notes.append((absolute_tick, note_number))
            elif event_type in (0xC0, 0xD0):
                position += 1
            else:
                break

        return track_name, notes, tempo_events

    def _read_variable_length(self, data: bytes, position: int):
        value = 0
        while True:
            current = data[position]
            position += 1
            value = (value << 7) | (current & 0x7F)
            if not (current & 0x80):
                return value, position

    def _build_notes_from_track(self, track_notes, converter, note_map):
        """Convierte ticks MIDI a segundos y mapea a zonas de batería."""
        notes = []
        for tick, midi_note in track_notes:
            zone = note_map.get(midi_note)
            if zone is None:
                continue
            notes.append(Note(time=converter.tick_to_seconds(tick), zone=zone))
        return notes

    def _build_inferred_kicks(self, bass_track_notes, converter):
        kick_source_notes = {96, 100}
        kicks = []
        for tick, midi_note in bass_track_notes:
            if midi_note not in kick_source_notes:
                continue
            kicks.append(Note(time=converter.tick_to_seconds(tick), zone="bombo"))
        return kicks

    def _build_grid_kicks(self, converter, length_seconds):
        kicks = []
        second = 0.0
        while second < length_seconds:
            kicks.append(Note(time=second, zone="bombo"))
            second += 1.0
        return kicks

    def _cleanup_real_drum_chart(self, notes):
        cleaned = []
        last_zone_times = {}

        for note in sorted(notes, key=lambda current: current.time):
            min_gap = 0.06 if note.zone == "bombo" else 0.045
            last_zone_time = last_zone_times.get(note.zone, -999.0)

            if note.time - last_zone_time < min_gap:
                continue

            cleaned.append(note)
            last_zone_times[note.zone] = note.time

        return cleaned

    def _adapt_guitar_chart_to_drums(self, guitar_notes, bass_notes):
        adapted = []
        clusters = []

        for note in sorted(guitar_notes, key=lambda current: current.time):
            if not clusters or note.time - clusters[-1][-1].time > ADAPT_CLUSTER_WINDOW_SECONDS:
                clusters.append([note])
            else:
                clusters[-1].append(note)

        last_main_hit_time = -999.0
        last_accent_time = -999.0
        recent_zone = None

        for cluster in clusters:
            cluster_time = cluster[0].time
            if cluster_time - last_main_hit_time < DRUM_FRIENDLY_NOTE_GAP_SECONDS:
                continue

            zone = self._choose_drum_zone_for_cluster(cluster, cluster_time, last_accent_time, recent_zone)
            adapted.append(Note(time=cluster_time, zone=zone))

            if zone in ("platillo", "tom superior", "tom inferior"):
                last_accent_time = cluster_time

            recent_zone = zone
            last_main_hit_time = cluster_time

        kicks = self._build_kick_track_from_bass(bass_notes)
        adapted.extend(kicks)
        adapted.sort(key=lambda note: note.time)
        return adapted

    def _choose_drum_zone_for_cluster(self, cluster, cluster_time, last_accent_time, recent_zone):
        zones = {note.zone for note in cluster}
        zone_counts = {}
        for note in cluster:
            zone_counts[note.zone] = zone_counts.get(note.zone, 0) + 1

        if cluster_time - last_accent_time > 1.2 and ("platillo" in zones or "tom inferior" in zones):
            return "platillo"

        if "tarola" in zones:
            return "tarola"

        if zone_counts.get("hithat", 0) >= 2:
            return "hithat"

        if "tom superior" in zones and recent_zone != "tom superior":
            return "tom superior"

        if "tom inferior" in zones and recent_zone != "tom inferior":
            return "tom inferior"

        if "platillo" in zones and recent_zone != "platillo":
            return "platillo"

        if "hithat" in zones:
            return "hithat"

        priority = ["tarola", "tom superior", "tom inferior", "platillo", "hithat"]
        for zone in priority:
            if zone in zones:
                return zone

        return cluster[0].zone

    def _build_kick_track_from_bass(self, bass_notes):
        if not bass_notes:
            return []

        kicks = []
        last_kick_time = -999.0
        bass_clusters = []

        for note in sorted(bass_notes, key=lambda current: current.time):
            if not bass_clusters or note.time - bass_clusters[-1][-1].time > 0.12:
                bass_clusters.append([note])
            else:
                bass_clusters[-1].append(note)

        for cluster_index, cluster in enumerate(bass_clusters):
            note = cluster[0]
            cluster_time = note.time
            is_strong_bass_hit = any(item.zone in ("platillo", "tom inferior") for item in cluster)
            is_phrase_pulse = cluster_index % 2 == 0
            min_gap = DRUM_FRIENDLY_KICK_GAP_SECONDS + (0.08 if not is_strong_bass_hit else 0.0)

            if cluster_time - last_kick_time < min_gap:
                continue

            if not is_strong_bass_hit and not is_phrase_pulse:
                continue

            kicks.append(Note(time=cluster_time, zone="bombo"))
            last_kick_time = cluster_time

        return kicks

    def _clone_notes(self, notes):
        return [Note(time=note.time, zone=note.zone) for note in notes]

    def build_playable_notes(self, base_notes, difficulty_key):
        profile = DIFFICULTY_PROFILES.get(difficulty_key, DIFFICULTY_PROFILES["easy"])
        notes = self._clone_notes(base_notes)
        notes = self._simplify_for_drums(notes, profile)
        notes = self._augment_notes_for_difficulty(notes, profile)
        notes.sort(key=lambda note: note.time)
        return self._simplify_for_drums(notes, profile)

    def _augment_notes_for_difficulty(self, notes, profile: DifficultyProfile):
        if profile.gap_fill_max_notes <= 0 or not profile.fill_zones:
            return notes

        augmented = self._clone_notes(notes)
        non_kick_notes = [note for note in notes if note.zone != "bombo"]
        fill_zone_index = 0

        for previous_note, next_note in zip(non_kick_notes, non_kick_notes[1:]):
            gap = next_note.time - previous_note.time
            if gap < profile.gap_fill_threshold_seconds:
                continue

            extra_notes = min(profile.gap_fill_max_notes, max(1, int(gap / profile.gap_fill_threshold_seconds)))
            for insert_index in range(extra_notes):
                insert_time = previous_note.time + (gap * ((insert_index + 1) / (extra_notes + 1)))
                if self._has_nearby_note(augmented, insert_time, 0.12):
                    continue

                zone = profile.fill_zones[fill_zone_index % len(profile.fill_zones)]
                fill_zone_index += 1
                if previous_note.zone == zone and next_note.zone == zone:
                    continue
                augmented.append(Note(time=insert_time, zone=zone))

        return augmented

    def _has_nearby_note(self, notes, target_time: float, threshold: float):
        for note in notes:
            if abs(note.time - target_time) <= threshold:
                return True
        return False

    def _simplify_for_drums(self, notes, profile: DifficultyProfile | None = None):
        """Filtra notas muy densas para que el chart sea tocable en batería."""
        profile = profile or DIFFICULTY_PROFILES["easy"]
        reduced = []
        last_global_time = -999.0
        last_zone_times = {}
        last_non_kick_time = -999.0
        recent_times = []
        recent_kick_times = []

        for note in notes:
            min_gap = profile.kick_gap_seconds if note.zone == "bombo" else profile.note_gap_seconds
            last_zone_time = last_zone_times.get(note.zone, -999.0)

            if note.time - last_zone_time < min_gap:
                continue

            if note.zone == "bombo":
                recent_kick_times = [t for t in recent_kick_times if note.time - t <= profile.kick_density_window_seconds]
                if len(recent_kick_times) >= profile.max_kicks_in_window:
                    continue

            if note.zone != "bombo" and note.time - last_non_kick_time < profile.chord_window_seconds:
                continue

            if note.zone != "bombo" and note.time - last_global_time < profile.global_min_gap_seconds:
                continue

            # Global density cap: allow at most DRUM_MAX_NOTES_PER_SECOND hits in the last second
            recent_times = [t for t in recent_times if note.time - t <= 1.0]
            if len(recent_times) >= profile.max_notes_per_second:
                continue

            reduced.append(note)
            last_zone_times[note.zone] = note.time
            last_global_time = note.time
            if note.zone != "bombo":
                last_non_kick_time = note.time
            else:
                recent_kick_times.append(note.time)
            recent_times.append(note.time)

        return reduced

    def convert_midi_to_drum_midi(self, midi_path: Path, output_path: Path):
        midi_data = midi_path.read_bytes()
        if midi_data[:4] != b"MThd":
            raise ValueError("Not a valid MIDI file")

        header_length = int.from_bytes(midi_data[4:8], "big")
        track_count = int.from_bytes(midi_data[10:12], "big")
        division = int.from_bytes(midi_data[12:14], "big")

        track_position = 8 + header_length
        tempo_events = [(0, 500000)]
        track_notes = {}

        for _ in range(track_count):
            if midi_data[track_position:track_position + 4] != b"MTrk":
                break

            track_length = int.from_bytes(midi_data[track_position + 4:track_position + 8], "big")
            track_bytes = midi_data[track_position + 8:track_position + 8 + track_length]
            track_position += 8 + track_length

            track_name, notes, tempos = self._parse_track(track_bytes)
            if track_name:
                track_notes[track_name] = notes
            tempo_events.extend(tempos)

        converter = MidiTempoConverter(division, tempo_events)

        # Build drum notes
        if "PART DRUMS" in track_notes:
            notes = self._build_notes_from_track(track_notes["PART DRUMS"], converter, DRUM_EXPERT_MAP)
        elif "PART GUITAR" in track_notes:
            guitar_notes = self._build_notes_from_track(track_notes["PART GUITAR"], converter, GUITAR_EXPERT_MAP)
            bass_notes = self._build_notes_from_track(track_notes.get("PART BASS", []), converter, GUITAR_EXPERT_MAP)
            notes = self._adapt_guitar_chart_to_drums(guitar_notes, bass_notes)
        else:
            notes = []

        # Apply simplification to avoid dense charts
        notes = self._simplify_for_drums(notes, DIFFICULTY_PROFILES["easy"])

        # Map zones to MIDI percussion note numbers (reverse mapping of DRUM_EXPERT_MAP)
        zone_to_midi = {
            "bombo": 96,
            "tarola": 97,
            "hithat": 98,
            "tom superior": 99,
            "platillo": 100,
            "tom inferior": 101,
        }

        # Build track bytes: single track with tempo meta at start and note on/off events
        def write_varlen(value: int):
            buffer = bytearray()
            buffer.append(value & 0x7F)
            value >>= 7
            parts = []
            while value:
                parts.append(0x80 | (value & 0x7F))
                value >>= 7
            for p in reversed(parts):
                buffer.insert(0, p)
            return bytes(buffer)

        events = bytearray()

        # Build ordered event list: (tick, priority, bytes)
        event_list = []

        # Track name meta at tick 0
        name_bytes = b"PART DRUMS"
        event_list.append((0, 0, b"\xFF\x03" + bytes([len(name_bytes)]) + name_bytes))

        # Tempo events (dedup by tick, keep last tempo at each tick)
        tempo_map = {}
        for tick, tempo in tempo_events:
            tempo_map[tick] = tempo
        if 0 not in tempo_map:
            tempo_map[0] = 500000

        for tick, tempo in sorted(tempo_map.items(), key=lambda item: item[0]):
            event_list.append((tick, 1, b"\xFF\x51\x03" + tempo.to_bytes(3, "big")))

        # Note on/off pairs
        note_on_status = 0x99  # channel 10 (percussion), note on
        note_off_status = 0x89
        for note in sorted(notes, key=lambda n: n.time):
            tick = converter.seconds_to_tick(note.time)
            midi_note = zone_to_midi.get(note.zone, 100)
            event_list.append((tick, 2, bytes([note_on_status, midi_note, 100])))
            off_tick = tick + max(1, int(division / 8))
            event_list.append((off_tick, 3, bytes([note_off_status, midi_note, 0])))

        # Sort events by tick then priority
        event_list.sort(key=lambda item: (item[0], item[1]))

        # Write events with proper delta timing
        last_tick = 0
        for tick, _priority, payload in event_list:
            delta = max(0, tick - last_tick)
            events += write_varlen(delta)
            events += payload
            last_tick = tick

        # End of track
        events += write_varlen(0)
        events += b"\xFF\x2F\x00"

        # Build file bytes
        header = bytearray()
        header += b"MThd"
        header += (6).to_bytes(4, "big")
        header += (0).to_bytes(2, "big")  # format 0
        header += (1).to_bytes(2, "big")  # one track
        header += division.to_bytes(2, "big")

        track_chunk = bytearray()
        track_chunk += b"MTrk"
        track_chunk += len(events).to_bytes(4, "big")
        track_chunk += events

        out_bytes = bytes(header + track_chunk)
        output_path.write_bytes(out_bytes)
        return output_path

    def _song_length_seconds(self, notes):
        if not notes:
            return 0.0
        return max(note.time for note in notes) + 2.5

    def _build_demo_song(self):
        pattern = [
            ("bombo", 0.00),
            ("tarola", 0.35),
            ("bombo", 0.60),
            ("hithat", 0.90),
            ("bombo", 1.20),
            ("platillo", 1.50),
            ("bombo", 1.80),
            ("tom superior", 2.05),
            ("tarola", 2.35),
            ("bombo", 2.70),
            ("tom inferior", 3.00),
            ("bombo", 3.25),
        ]

        notes = []
        start_time = 1.2
        section_length = 3.7

        for section in range(6):
            section_offset = start_time + (section * section_length)
            for zone, note_offset in pattern:
                notes.append(Note(time=section_offset + note_offset, zone=zone))

        return SongData(
            title="Demo Song",
            artist="AirDrums",
            album="Demo",
            charter="Codex",
            audio_path=None,
            cover_path=None,
            vocals_path=None,
            rhythm_path=None,
            drums_paths=[],
            base_notes=self._clone_notes(notes),
            notes=notes,
            length_seconds=max(note.time for note in notes) + 4.0,
            source_name="DEMO",
            inferred_kick=False,
        )


class MidiTempoConverter:
    """Convierte entre ticks MIDI y segundos usando eventos de tempo."""
    def __init__(self, division: int, tempo_events):
        self.division = division
        self.markers = []

        ordered_events = {}
        for tick, tempo in sorted(tempo_events, key=lambda event: event[0]):
            ordered_events[tick] = tempo

        current_tempo = 500000
        current_tick = 0
        current_seconds = 0.0
        self.markers.append((0, 0.0, current_tempo))

        for tick, tempo in sorted(ordered_events.items()):
            if tick == 0:
                current_tempo = tempo
                self.markers[0] = (0, 0.0, current_tempo)
                continue

            delta_ticks = tick - current_tick
            current_seconds += (delta_ticks * current_tempo) / (division * 1_000_000.0)
            current_tick = tick
            current_tempo = tempo
            self.markers.append((current_tick, current_seconds, current_tempo))

        self.ticks = [marker[0] for marker in self.markers]

    def tick_to_seconds(self, target_tick: int):
        """Convierte ticks MIDI a segundos reales."""
        marker_index = bisect_right(self.ticks, target_tick) - 1
        marker_tick, marker_seconds, marker_tempo = self.markers[max(0, marker_index)]
        delta_ticks = target_tick - marker_tick
        return marker_seconds + ((delta_ticks * marker_tempo) / (self.division * 1_000_000.0))

    def seconds_to_tick(self, seconds: float):
        """Convierte segundos a ticks MIDI (aproximado)."""
        # Find the marker that applies at this time
        index = bisect_right([m[1] for m in self.markers], seconds) - 1
        index = max(0, index)
        marker_tick, marker_seconds, marker_tempo = self.markers[index]
        delta_seconds = max(0.0, seconds - marker_seconds)
        ticks = marker_tick + int((delta_seconds * 1_000_000.0 * self.division) / marker_tempo)
        return ticks


class RhythmGame:
    """Loop principal del juego: input, lógica, dibujo y audio."""
    def __init__(self):
        # Pre-inicializar el mixer con un búfer bajo (512) para eliminar el retraso de audio
        pygame.mixer.pre_init(48000, -16, 2, 512)
        pygame.init()
        pygame.mixer.init()
        pygame.mixer.set_num_channels(8)
        # --- NUEVO CÓDIGO ---
        # 1. Obtiene la resolución de la pantalla principal
        minfo = pygame.display.Info()
        screen_width = minfo.current_w
        screen_height = minfo.current_w
        
        # 2. Calcula para que ocupe la mitad derecha
        half_width = screen_width // 2
        
        # 3. Le indica a SDL (Pygame) en qué coordenada colocar la ventana
        # 'x,y' -> arranca en el centro (anchura / 2) y en lo más alto (0)
        os.environ['SDL_VIDEO_WINDOW_POS'] = f"{half_width},0"
        
        # 4. Asigna el nuevo tamaño (mitad del ancho, alto completo)
        global WINDOW_WIDTH, WINDOW_HEIGHT
        WINDOW_WIDTH = half_width
        WINDOW_HEIGHT = minfo.current_h
        # --------------------
        
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        pygame.display.set_caption(APP_TITLE)

        # === SUPERFICIES PRE-CALCULABLES CREADAS EN EL INIT ===
        
        # Sombra resplandeciente del fondo (Glow surface)
        self.glow_surface = pygame.Surface((WINDOW_WIDTH + 80, WINDOW_HEIGHT + 110), pygame.SRCALPHA)
        
        # Superficie base transparente usada para clonar otras si hace falta
        self.blank_surface = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
        self.crack_overlay = None  # Se pre-creará sólo si falla la partida
        self.danger_overlay = None # Se pre-creará sólo si falla la partida
        
        # Resplandor en la zona de meta donde das los golpes (strike glow)
        self.strike_glow = pygame.Surface((WINDOW_WIDTH + 20, 26), pygame.SRCALPHA)
        pygame.draw.rect(self.strike_glow, (255, 248, 212, 90), self.strike_glow.get_rect(), border_radius=10)
        
        # Inicializando un caché para no re-crear la sombra redonda de cada nota (shadow_surface)
        # Esto guarda texturas de la sombra y tamaño por cada color
        self.note_shadow_surfaces = {}

        self.clock = pygame.time.Clock()
        self.song_loader = SongLoader()

        self.title_font = pygame.font.SysFont("arial", 42, bold=True)
        self.ui_font = pygame.font.SysFont("arial", 24, bold=True)
        self.small_font = pygame.font.SysFont("arial", 18)
        self.tiny_font = pygame.font.SysFont("arial", 16)

        self.receiver = UdpHitReceiver(UDP_HOST, UDP_PORT)
        self.song_library = self.song_loader.load_all_songs()
        self.selected_song_index = 0
        self.selected_difficulty = "easy"
        self.song_data = None
        self.notes = []
        self.song_length = 0.0
        self.cover_surface = None
        self.vocals_sound = None
        self.rhythm_sound = None
        self.drums_sounds = []
        self.vocals_channel = pygame.mixer.Channel(1)
        self.rhythm_channel = pygame.mixer.Channel(2)
        self.drums_channels = [pygame.mixer.Channel(3), pygame.mixer.Channel(4), pygame.mixer.Channel(5), pygame.mixer.Channel(6)]
        self._apply_song_selection(self.selected_song_index)

        self.state = "main_menu"
        self.song_started_at = None
        self.music_started = False
        self.running = True
        self.pause_started_at = None
        self.accumulated_pause_seconds = 0.0
        self.return_state = "main_menu"
        self.command_buffer = ""
        self.command_feedback = "Escribe un comando y presiona ENTER"
        self.command_feedback_until = 0.0
        self.main_menu_options = ["Jugar", "Calibrar", "Creditos", "Salir"]
        self.main_menu_index = 0
        self.pause_menu_options = ["Continuar", "Reiniciar", "Cambiar nivel", "Salir"]
        self.pause_menu_index = 0
        self.pause_difficulty_options = ["Facil", "Normal", "Dificil", "Atras"]
        self.pause_difficulty_index = 0
        self.confirm_options = ["Si", "No"]
        self.confirm_index = 1
        self.confirm_context = "return_to_pause"
        self.voice_listener = None
        self.voice_backend_name = self._detect_voice_backend_name()

        self.score = 0
        self.combo = 0
        self.best_combo = 0
        self.health = 0.50
        self.display_health = self.health
        self.miss_streak = 0
        self.last_hit_zone = None
        self.last_hit_at = -999.0
        self.last_judgement = "Listo para tocar"
        self.failure_started_at = None
        self.failed_message = ""
        if "microfono" in self.voice_backend_name.lower():
            self.command_feedback = "Voz activa: menu o pausa en partida; jugar, calibrar, creditos, salir"
        elif "micro no configurado" in self.voice_backend_name.lower():
            self.command_feedback = "El micro no esta activo aqui: faltan speech_recognition y pyaudio"

    def _detect_voice_backend_name(self):
        if getattr(self, "voice_listener", None) is not None and self.voice_listener.available:
            return self.voice_listener.backend_name
        has_speech = importlib.util.find_spec("speech_recognition") is not None
        has_audio = importlib.util.find_spec("pyaudio") is not None
        if has_speech and has_audio:
            return "Microfono"
        return "Texto (micro no configurado)"

    def run(self):
        """Bucle principal de render + update."""
        try:
            while self.running:
                dt = self.clock.tick(FPS) / 1000.0
                self._handle_events()
                self._update(dt)
                self._draw()
        finally:
            pygame.mixer.music.stop()
            self.voice_listener.stop()
            self.receiver.close()
            pygame.quit()

    def _load_cover_surface(self, cover_path: Path | None):
        if cover_path is None or not cover_path.exists():
            return None

        try:
            surface = pygame.image.load(str(cover_path)).convert_alpha()
        except pygame.error:
            return None

        return pygame.transform.smoothscale(surface, (190, 190))

    def _handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
                return

            if event.type == pygame.TEXTINPUT:
                if self._supports_command_input():
                    self.command_buffer += event.text
                continue

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    if self.state == "playing":
                        self._pause_game()
                    elif self.state == "paused":
                        self._resume_game()
                    elif self.state == "pause_difficulty":
                        self.state = "paused"
                    elif self.state == "confirm":
                        self._cancel_confirmation()
                    elif self.state in ("song_select", "credits"):
                        self._go_to_main_menu()
                    else:
                        self.running = False
                    return

                if self._supports_command_input():
                    if event.key == pygame.K_BACKSPACE:
                        self.command_buffer = self.command_buffer[:-1]
                        continue
                    if event.key == pygame.K_RETURN and self.command_buffer.strip():
                        self._submit_command(self.command_buffer)
                        self.command_buffer = ""
                        continue

                if self.state == "main_menu" and event.key == pygame.K_DOWN:
                    self.main_menu_index = (self.main_menu_index + 1) % len(self.main_menu_options)
                    continue

                if self.state == "main_menu" and event.key == pygame.K_UP:
                    self.main_menu_index = (self.main_menu_index - 1) % len(self.main_menu_options)
                    continue

                if self.state == "main_menu" and event.key in (pygame.K_RETURN, pygame.K_SPACE):
                    self._activate_main_menu_option(self.main_menu_options[self.main_menu_index])
                    continue

                if self.state == "song_select" and event.key == pygame.K_DOWN:
                    self._apply_song_selection(self.selected_song_index + 1)
                    continue

                if self.state == "song_select" and event.key == pygame.K_UP:
                    self._apply_song_selection(self.selected_song_index - 1)
                    continue

                if self.state == "song_select" and event.key in (pygame.K_LEFT, pygame.K_q):
                    self._cycle_difficulty(-1)
                    continue

                if self.state == "song_select" and event.key in (pygame.K_RIGHT, pygame.K_e):
                    self._cycle_difficulty(1)
                    continue

                if self.state == "song_select" and event.key == pygame.K_1:
                    self._set_difficulty("easy")
                    continue

                if self.state == "song_select" and event.key == pygame.K_2:
                    self._set_difficulty("medium")
                    continue

                if self.state == "song_select" and event.key == pygame.K_3:
                    self._set_difficulty("hard")
                    continue

                if self.state == "song_select" and event.key in (pygame.K_RETURN, pygame.K_SPACE):
                    self._start_song()
                    continue

                if self.state in ("finished", "failed") and event.key == pygame.K_RETURN:
                    self._restart_song()
                    continue

                if self.state == "playing" and event.key == pygame.K_m:
                    self._pause_game()
                    continue

                if self.state == "paused" and event.key == pygame.K_DOWN:
                    self.pause_menu_index = (self.pause_menu_index + 1) % len(self.pause_menu_options)
                    continue

                if self.state == "paused" and event.key == pygame.K_UP:
                    self.pause_menu_index = (self.pause_menu_index - 1) % len(self.pause_menu_options)
                    continue

                if self.state == "paused" and event.key in (pygame.K_RETURN, pygame.K_SPACE):
                    self._activate_pause_menu_option(self.pause_menu_options[self.pause_menu_index])
                    continue

                if self.state == "pause_difficulty" and event.key == pygame.K_DOWN:
                    self.pause_difficulty_index = (self.pause_difficulty_index + 1) % len(self.pause_difficulty_options)
                    continue

                if self.state == "pause_difficulty" and event.key == pygame.K_UP:
                    self.pause_difficulty_index = (self.pause_difficulty_index - 1) % len(self.pause_difficulty_options)
                    continue

                if self.state == "pause_difficulty" and event.key in (pygame.K_RETURN, pygame.K_SPACE):
                    self._activate_pause_difficulty_option(self.pause_difficulty_options[self.pause_difficulty_index])
                    continue

                if self.state == "confirm" and event.key == pygame.K_LEFT:
                    self.confirm_index = (self.confirm_index - 1) % len(self.confirm_options)
                    continue

                if self.state == "confirm" and event.key == pygame.K_RIGHT:
                    self.confirm_index = (self.confirm_index + 1) % len(self.confirm_options)
                    continue

                if self.state == "confirm" and event.key in (pygame.K_RETURN, pygame.K_SPACE):
                    self._resolve_confirmation(self.confirm_options[self.confirm_index] == "Si")
                    continue

                zone = KEYBOARD_ZONE_MAP.get(event.key)
                if zone and self.state == "playing":
                    self._register_hit(zone)

    def _update(self, dt: float):
        current_time = pygame.time.get_ticks() / 1000.0
        self.display_health += (self.health - self.display_health) * min(1.0, dt * 8.0)

        if self.command_feedback_until and current_time > self.command_feedback_until:
            self.command_feedback_until = 0.0
            self.command_feedback = "Escribe un comando y presiona ENTER"

        if self.voice_listener is not None:
            voice_command = self.voice_listener.poll_command()
            if voice_command:
                self._submit_command(voice_command)
                self._show_command_feedback(f"Voz: {voice_command}")

        if self.state == "playing":
            if self.song_started_at is not None and current_time >= self.song_started_at and not self.music_started:
                self._start_music()

            for zone in self.receiver.poll_hits():
                self._register_hit(zone)

            song_time = self._current_song_time()
            self._judge_missed_notes(song_time)

            if song_time >= self.song_length:
                self.state = "finished"
                pygame.mixer.music.stop()

    def _supports_command_input(self):
        return self.state in {
            "main_menu",
            "song_select",
            "credits",
            "paused",
            "pause_difficulty",
            "confirm",
            "finished",
            "failed",
        }

    def _normalize_command(self, command: str):
        lowered = command.strip().lower()
        normalized = unicodedata.normalize("NFD", lowered)
        normalized = "".join(char for char in normalized if unicodedata.category(char) != "Mn")
        normalized = " ".join(normalized.split())
        number_aliases = {
            "normal": "medio",
        }
        return number_aliases.get(normalized, normalized)

    def _show_command_feedback(self, message: str, duration: float = 2.6):
        self.command_feedback = message
        self.command_feedback_until = (pygame.time.get_ticks() / 1000.0) + duration

    def _submit_command(self, raw_command: str):
        command = self._normalize_command(raw_command)
        if not command:
            return

        handled = False
        if self.state == "main_menu":
            handled = self._handle_main_menu_command(command)
        elif self.state == "song_select":
            handled = self._handle_song_select_command(command)
        elif self.state == "playing":
            handled = self._handle_playing_command(command)
        elif self.state == "paused":
            handled = self._handle_paused_command(command)
        elif self.state == "pause_difficulty":
            handled = self._handle_pause_difficulty_command(command)
        elif self.state == "confirm":
            handled = self._handle_confirm_command(command)
        elif self.state == "credits":
            handled = self._handle_credits_command(command)
        elif self.state in ("finished", "failed"):
            handled = self._handle_end_state_command(command)

        if not handled:
            self._show_command_feedback(f"No entendi '{raw_command.strip()}'")

    def _command_matches(self, command: str, *options: str):
        return command in options

    def _handle_main_menu_command(self, command: str):
        if self._command_matches(command, "jugar"):
            self._activate_main_menu_option("Jugar")
            return True
        if self._command_matches(command, "calibrar", "calibracion"):
            self._activate_main_menu_option("Calibrar")
            return True
        if self._command_matches(command, "creditos", "creditos del juego"):
            self._activate_main_menu_option("Creditos")
            return True
        if self._command_matches(command, "salir"):
            self._activate_main_menu_option("Salir")
            return True
        return False

    def _handle_song_select_command(self, command: str):
        if self._command_matches(command, "siguiente"):
            self._apply_song_selection(self.selected_song_index + 1)
            return True
        if self._command_matches(command, "atras", "anterior"):
            self._apply_song_selection(self.selected_song_index - 1)
            return True
        if self._command_matches(command, "volver", "menu"):
            self._go_to_main_menu()
            return True
        if self._command_matches(command, "jugar", "empezar"):
            self._start_song()
            return True
        if self._command_matches(command, "facil"):
            self._set_difficulty("easy")
            self._show_command_feedback("Dificultad: Facil")
            return True
        if self._command_matches(command, "medio"):
            self._set_difficulty("medium")
            self._show_command_feedback("Dificultad: Medio")
            return True
        if self._command_matches(command, "dificil"):
            self._set_difficulty("hard")
            self._show_command_feedback("Dificultad: Dificil")
            return True
        return False

    def _handle_playing_command(self, command: str):
        if self._command_matches(command, "menu", "pausa", "pause", "parar", "detener"):
            self._pause_game()
            return True
        return False

    def _handle_paused_command(self, command: str):
        if self._command_matches(command, "continuar"):
            self._resume_game()
            return True
        if self._command_matches(command, "reiniciar"):
            self._restart_current_song()
            return True
        if self._command_matches(command, "cambiar nivel", "nivel", "dificultad"):
            self.state = "pause_difficulty"
            return True
        if self._command_matches(command, "salir"):
            self._open_exit_confirmation("return_to_song_select")
            return True
        if self._command_matches(command, "volver"):
            self._resume_game()
            return True
        return False

    def _handle_pause_difficulty_command(self, command: str):
        if self._command_matches(command, "facil"):
            self._set_difficulty("easy")
            self._restart_current_song()
            return True
        if self._command_matches(command, "normal", "medio"):
            self._set_difficulty("medium")
            self._restart_current_song()
            return True
        if self._command_matches(command, "dificil"):
            self._set_difficulty("hard")
            self._restart_current_song()
            return True
        if self._command_matches(command, "atras", "volver"):
            self.state = "paused"
            return True
        return False

    def _handle_confirm_command(self, command: str):
        if self._command_matches(command, "si"):
            self._resolve_confirmation(True)
            return True
        if self._command_matches(command, "no", "atras", "volver"):
            self._resolve_confirmation(False)
            return True
        return False

    def _handle_credits_command(self, command: str):
        if self._command_matches(command, "volver", "atras", "menu"):
            self._go_to_main_menu()
            return True
        return False

    def _handle_end_state_command(self, command: str):
        if self._command_matches(command, "volver", "menu"):
            self._restart_song()
            return True
        if self._command_matches(command, "reiniciar"):
            self._start_song()
            return True
        return False

    def _activate_main_menu_option(self, option: str):
        if option == "Jugar":
            self.state = "song_select"
            self._show_command_feedback("Menu de canciones listo")
        elif option == "Calibrar":
            self._run_calibration_flow()
        elif option == "Creditos":
            self.state = "credits"
        elif option == "Salir":
            self.running = False

    def _activate_pause_menu_option(self, option: str):
        if option == "Continuar":
            self._resume_game()
        elif option == "Reiniciar":
            self._restart_current_song()
        elif option == "Cambiar nivel":
            self.state = "pause_difficulty"
        elif option == "Salir":
            self._open_exit_confirmation("return_to_song_select")

    def _activate_pause_difficulty_option(self, option: str):
        if option == "Facil":
            self._set_difficulty("easy")
            self._restart_current_song()
        elif option == "Normal":
            self._set_difficulty("medium")
            self._restart_current_song()
        elif option == "Dificil":
            self._set_difficulty("hard")
            self._restart_current_song()
        elif option == "Atras":
            self.state = "paused"

    def _pause_game(self):
        if self.state != "playing":
            return
        self.state = "paused"
        self.pause_menu_index = 0
        self.pause_started_at = pygame.time.get_ticks() / 1000.0
        pygame.mixer.music.pause()
        try:
            self.vocals_channel.pause()
            self.rhythm_channel.pause()
            for channel in self.drums_channels:
                channel.pause()
        except Exception:
            pass

    def _resume_game(self):
        if self.state != "paused":
            return
        if self.pause_started_at is not None:
            self.accumulated_pause_seconds += (pygame.time.get_ticks() / 1000.0) - self.pause_started_at
            self.pause_started_at = None
        self.state = "playing"
        pygame.mixer.music.unpause()
        try:
            self.vocals_channel.unpause()
            self.rhythm_channel.unpause()
            for channel in self.drums_channels:
                channel.unpause()
        except Exception:
            pass

    def _restart_current_song(self):
        pygame.mixer.music.stop()
        try:
            self.vocals_channel.stop()
            self.rhythm_channel.stop()
            for channel in self.drums_channels:
                channel.stop()
        except Exception:
            pass
        self._start_song()

    def _open_exit_confirmation(self, context: str):
        self.return_state = "paused"
        self.confirm_context = context
        self.confirm_index = 1
        self.state = "confirm"

    def _cancel_confirmation(self):
        self.state = self.return_state

    def _resolve_confirmation(self, accepted: bool):
        if not accepted:
            self._cancel_confirmation()
            return
        if self.confirm_context == "return_to_song_select":
            self._exit_to_song_select()

    def _go_to_main_menu(self):
        self.state = "main_menu"
        self.main_menu_index = 0
        self.command_buffer = ""

    def _exit_to_song_select(self):
        pygame.mixer.music.stop()
        try:
            self.vocals_channel.stop()
            self.rhythm_channel.stop()
            for channel in self.drums_channels:
                channel.stop()
        except Exception:
            pass
        self.song_started_at = None
        self.music_started = False
        self.failure_started_at = None
        self.pause_started_at = None
        self.accumulated_pause_seconds = 0.0
        self.state = "song_select"

    def _run_calibration_flow(self):
        if not TRACKING_SCRIPT_PATH.exists():
            self._show_command_feedback("No encontre tracking-service/main.py")
            return
        self._show_command_feedback("Abriendo calibracion externa...")
        try:
            subprocess.run([sys.executable, str(TRACKING_SCRIPT_PATH)], cwd=str(TRACKING_SCRIPT_PATH.parent), check=False)
        except Exception:
            self._show_command_feedback("No pude abrir la calibracion")
            return
        self.state = "main_menu"
        self._show_command_feedback("Calibracion cerrada. Regresaste al menu")

    def _start_song(self):
        # --- PRECARGAR AUDIO ANTES DE INICIAR EL TIEMPO ---
        # Guardar estos textos estáticos como imágenes en la memoria
        self.cached_song_label = self.ui_font.render(f"{self.song_data.artist} - {self.song_data.title}", True, HUD_TEXT)
        self.cached_legend_label = self.small_font.render(
            "Platillo | Hi-Hat | Tarola | Tom superior | Tom inferior | Bombo", 
            True, HUD_TEXT
        )
        if self.song_data.audio_path is not None:
            pygame.mixer.music.load(str(self.song_data.audio_path))
            
            try:
                if self.song_data.vocals_path is not None:
                    self.vocals_sound = pygame.mixer.Sound(str(self.song_data.vocals_path))
            except Exception:
                self.vocals_sound = None

            try:
                if self.song_data.rhythm_path is not None:
                    self.rhythm_sound = pygame.mixer.Sound(str(self.song_data.rhythm_path))
            except Exception:
                self.rhythm_sound = None

            self.drums_sounds = []
            for drum_path in self.song_data.drums_paths[:4]:
                try:
                    self.drums_sounds.append(pygame.mixer.Sound(str(drum_path)))
                except Exception:
                    continue
        # --------------------------------------------------

        self.state = "playing"
        # ¡IMPORTANTE! El tiempo se calcula ahora DESPUÉS de cargar la memoria
        self.song_started_at = (pygame.time.get_ticks() / 1000.0) + START_DELAY_SECONDS
        self.music_started = False
        self.pause_started_at = None
        self.accumulated_pause_seconds = 0.0
        self._reset_run_stats()

        for note in self.notes:
            note.hit = False
            note.judged = False

    def _restart_song(self):
        pygame.mixer.music.stop()
        try:
            self.vocals_channel.stop()
        except Exception:
            pass
        try:
            self.rhythm_channel.stop()
        except Exception:
            pass
        for channel in self.drums_channels:
            try:
                channel.stop()
            except Exception:
                pass
        self.state = "song_select"
        self.song_started_at = None
        self.music_started = False
        self.failure_started_at = None
        self.pause_started_at = None
        self.accumulated_pause_seconds = 0.0

    def _apply_song_selection(self, index: int):
        if not self.song_library:
            return

        self.selected_song_index = index % len(self.song_library)
        self.song_data = self.song_library[self.selected_song_index]
        self._rebuild_notes_for_selected_difficulty()
        self.song_length = self.song_data.length_seconds
        self.cover_surface = self._load_cover_surface(self.song_data.cover_path)
        # Defer loading stems until playback to avoid blocking UI on selection
        self.vocals_sound = None
        self.rhythm_sound = None
        self.drums_sounds = []

    def _set_difficulty(self, difficulty_key: str):
        if difficulty_key not in DIFFICULTY_PROFILES:
            return
        self.selected_difficulty = difficulty_key
        self._rebuild_notes_for_selected_difficulty()

    def _cycle_difficulty(self, direction: int):
        current_index = DIFFICULTY_ORDER.index(self.selected_difficulty)
        new_index = (current_index + direction) % len(DIFFICULTY_ORDER)
        self._set_difficulty(DIFFICULTY_ORDER[new_index])

    def _rebuild_notes_for_selected_difficulty(self):
        if self.song_data is None:
            return
        self.notes = self.song_loader.build_playable_notes(self.song_data.base_notes, self.selected_difficulty)
        self.song_data.notes = self.notes

    def _reset_run_stats(self):
        self.score = 0
        self.combo = 0
        self.best_combo = 0
        self.health = 0.50
        self.display_health = self.health
        self.miss_streak = 0
        self.last_hit_zone = None
        self.last_hit_at = -999.0
        self.last_judgement = "Comienza"
        self.failure_started_at = None
        self.failed_message = ""
        self.pause_started_at = None

    def _current_profile(self):
        return DIFFICULTY_PROFILES[self.selected_difficulty]

    def _start_music(self):
        """Inicia el audio base y los stems (vocals/rhythm/drums) sincronizados."""
        if self.song_data.audio_path is None:
            self.music_started = True
            return

        try:
            pygame.mixer.music.play()
            
            if self.vocals_sound is not None:
                self.vocals_channel.play(self.vocals_sound)
                
            if self.rhythm_sound is not None:
                self.rhythm_channel.play(self.rhythm_sound)
                
            for index, sound in enumerate(self.drums_sounds):
                if index < len(self.drums_channels):
                    self.drums_channels[index].play(sound)
                    
        except pygame.error:
            pass

        self.music_started = True

    def _register_hit(self, zone: str):
        """Registra un golpe del usuario y evalúa timing/score."""
        if self.song_started_at is None or self.state != "playing":
            return

        current_time = pygame.time.get_ticks() / 1000.0
        song_time = current_time - self.song_started_at - GLOBAL_OFFSET_SECONDS

        candidate = None
        candidate_offset = None

        for note in self.notes:
            if note.judged or note.zone != zone:
                continue

            offset = song_time - note.time
            if offset < -EARLY_HIT_WINDOW_SECONDS or offset > LATE_HIT_WINDOW_SECONDS:
                continue

            if candidate is None or abs(offset) < abs(candidate_offset):
                candidate = note
                candidate_offset = offset

        if candidate is None:
            self.combo = 0
            has_upcoming_note = self._has_upcoming_note(zone, song_time)
            self.last_judgement = "Muy pronto" if has_upcoming_note else "Fuera de tiempo"
            self.miss_streak += 1
            penalty = self._current_profile().health_loss_bad_hit * (0.75 if has_upcoming_note else 1.0)
            self._change_health(-penalty)
            self.last_hit_zone = zone
            self.last_hit_at = current_time
            self._check_fail_state()
            return

        candidate.hit = True
        candidate.judged = True
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
        self.miss_streak = 0
        self._change_health(self._current_profile().health_gain_hit)
        self.score += max(50, int(150 - (abs(candidate_offset) * 500)))
        # Ampliamos la ventana de Perfecto de 55ms a 65ms
        self.last_judgement = "Perfecto" if abs(candidate_offset) < 0.075 else "Bien"        
        self.last_hit_zone = zone
        self.last_hit_at = current_time

    def _has_upcoming_note(self, zone: str, song_time: float):
        for note in self.notes:
            if note.judged or note.zone != zone:
                continue
            return note.time > song_time
        return False

    def _judge_missed_notes(self, song_time: float):
        for note in self.notes:
            if note.judged:
                continue

            if song_time - note.time > LATE_HIT_WINDOW_SECONDS:
                note.judged = True
                note.hit = False
                self.combo = 0
                self.miss_streak += 1
                self._change_health(-self._current_profile().health_loss_miss)
                self.last_judgement = "Miss"
                self._check_fail_state()
                if self.state == "failed":
                    break

    def _change_health(self, delta: float):
        self.health = max(0.0, min(1.0, self.health + delta))

    def _check_fail_state(self):
        profile = self._current_profile()
        if self.health > 0.0 and self.miss_streak < profile.fail_streak:
            return
        self._trigger_fail_state()

    def _trigger_fail_state(self):
        if self.state != "playing":
            return

        self.state = "failed"
        self.health = 0.0
        self.failure_started_at = pygame.time.get_ticks() / 1000.0
        if self.miss_streak >= self._current_profile().fail_streak:
            self.failed_message = "Demasiados fallos seguidos"
        else:
            self.failed_message = "Te quedaste sin energia"
        self.last_judgement = "Perdiste"
        pygame.mixer.music.stop()
        try:
            self.vocals_channel.stop()
        except Exception:
            pass
        try:
            self.rhythm_channel.stop()
        except Exception:
            pass
        for channel in self.drums_channels:
            try:
                channel.stop()
            except Exception:
                pass

    def _draw(self):
        self._draw_background()

        if self.state == "main_menu":
            self._draw_main_menu()
        elif self.state == "song_select":
            self._draw_song_select()
        elif self.state == "playing":
            self._draw_playfield()
        elif self.state == "paused":
            self._draw_playfield()
            self._draw_pause_overlay()
        elif self.state == "pause_difficulty":
            self._draw_playfield()
            self._draw_pause_overlay()
            self._draw_pause_difficulty_overlay()
        elif self.state == "confirm":
            if self.song_started_at is not None:
                self._draw_playfield()
            else:
                self._draw_song_select()
            self._draw_confirm_overlay()
        elif self.state == "failed":
            self._draw_playfield()
            self._draw_failed_overlay()
        elif self.state == "finished":
            self._draw_playfield()
            self._draw_results()
        elif self.state == "credits":
            self._draw_credits()

        self._draw_command_bar()
        pygame.display.flip()

    def _draw_background(self):
        for y in range(WINDOW_HEIGHT):
            blend = y / WINDOW_HEIGHT
            color = (
                int(BACKGROUND_TOP[0] + ((BACKGROUND_BOTTOM[0] - BACKGROUND_TOP[0]) * blend)),
                int(BACKGROUND_TOP[1] + ((BACKGROUND_BOTTOM[1] - BACKGROUND_TOP[1]) * blend)),
                int(BACKGROUND_TOP[2] + ((BACKGROUND_BOTTOM[2] - BACKGROUND_TOP[2]) * blend)),
            )
            pygame.draw.line(self.screen, color, (0, y), (WINDOW_WIDTH, y))

        elapsed = pygame.time.get_ticks() / 1000.0
        for index in range(8):
            center_x = 80 + (index * 165)
            wobble = math.sin(elapsed * 1.5 + index) * 40
            radius = 110 + int(20 * math.sin((elapsed * 2.4) + index))
            light_color = (
                min(255, 80 + (index * 10)),
                120 + int(30 * math.sin(elapsed + index)),
                70,
            )
            surface = pygame.Surface((radius * 2, radius * 2), pygame.SRCALPHA)
            pygame.draw.circle(surface, (*light_color, 24), (radius, radius), radius)
            self.screen.blit(surface, (center_x - radius, 120 + wobble - radius))

        if self.cover_surface is not None:
            overlay = pygame.Surface((260, 260), pygame.SRCALPHA)
            overlay.fill((0, 0, 0, 80))
            overlay_rect = overlay.get_rect(topright=(WINDOW_WIDTH - 40, 34))
            self.screen.blit(overlay, overlay_rect)
            self.screen.blit(self.cover_surface, self.cover_surface.get_rect(center=overlay_rect.center))

    def _draw_panel(self, rect: pygame.Rect, border_color=(240, 215, 120), fill_alpha=122, radius=18):
        shadow = pygame.Surface((rect.width + 16, rect.height + 16), pygame.SRCALPHA)
        pygame.draw.rect(shadow, (0, 0, 0, 80), shadow.get_rect(), border_radius=radius + 4)
        self.screen.blit(shadow, (rect.x - 8, rect.y + 6))

        panel = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
        pygame.draw.rect(panel, (12, 12, 18, fill_alpha), panel.get_rect(), border_radius=radius)
        pygame.draw.rect(panel, border_color, panel.get_rect(), 2, border_radius=radius)
        self.screen.blit(panel, rect.topleft)

    def _draw_main_menu(self):
        title = self.title_font.render("AirDrums Hero", True, HUD_TEXT)
        subtitle = self.ui_font.render("Menu principal", True, HUD_TEXT)
        helper = self.small_font.render("Di o escribe: Jugar, Calibrar, Creditos, Salir", True, HUD_TEXT)

        header_box = pygame.Rect(0, 0, min(620, WINDOW_WIDTH - 120), 120)
        header_box.center = (WINDOW_WIDTH // 2, 100)
        self._draw_panel(header_box, fill_alpha=115, radius=24)
        self.screen.blit(title, title.get_rect(center=(header_box.centerx, header_box.y + 40)))
        self.screen.blit(subtitle, subtitle.get_rect(center=(header_box.centerx, header_box.y + 82)))

        menu_box = pygame.Rect(0, 0, min(420, WINDOW_WIDTH - 180), 300)
        menu_box.center = (WINDOW_WIDTH // 2, int(WINDOW_HEIGHT * 0.38))
        self._draw_panel(menu_box, fill_alpha=108, radius=24)

        for index, option in enumerate(self.main_menu_options):
            option_rect = pygame.Rect(menu_box.x + 28, menu_box.y + 52 + (index * 58), menu_box.width - 56, 42)
            if index == self.main_menu_index:
                pygame.draw.rect(self.screen, (88, 76, 32), option_rect, border_radius=14)
                pygame.draw.rect(self.screen, (255, 235, 130), option_rect, 2, border_radius=14)
            option_surface = self.ui_font.render(option, True, HUD_TEXT)
            self.screen.blit(option_surface, option_surface.get_rect(center=option_rect.center))

        info_box = pygame.Rect(0, 0, min(720, WINDOW_WIDTH - 80), 60)
        info_box.center = (WINDOW_WIDTH // 2, WINDOW_HEIGHT - 140)
        self._draw_panel(info_box, fill_alpha=98, radius=18)
        self.screen.blit(helper, helper.get_rect(center=(info_box.centerx, info_box.centery)))

    def _draw_song_select(self):
        title = self.title_font.render("AirDrums Hero", True, HUD_TEXT)
        subtitle = self.ui_font.render(f"{self.song_data.artist} - {self.song_data.title}", True, HUD_TEXT)
        hint = self.ui_font.render("ENTER para empezar", True, HUD_TEXT)
        source = self.small_font.render(f"Chart: {self.song_data.source_name}", True, HUD_TEXT)
        kick_text = "Usando chart PART DRUMS (bateria)"
        kick_hint = self.small_font.render(kick_text, True, HUD_TEXT)
        difficulty_label = DIFFICULTY_PROFILES[self.selected_difficulty].label
        difficulty_hint = self.small_font.render(
            f"Dificultad: {difficulty_label}  |  Voz: Facil, Medio, Dificil",
            True,
            HUD_TEXT,
        )
        note_count_hint = self.small_font.render(
            f"Notas para esta dificultad: {len(self.notes)}",
            True,
            HUD_TEXT,
        )

        selector_box = pygame.Rect(28, 252, min(300, max(240, int(WINDOW_WIDTH * 0.28))), 250)
        content_left = selector_box.right + 28
        content_width = max(420, WINDOW_WIDTH - content_left - 34)

        header_box = pygame.Rect(content_left, 78, content_width, 112)
        self._draw_panel(header_box, fill_alpha=110, radius=24)
        self.screen.blit(title, title.get_rect(center=(header_box.centerx, header_box.y + 40)))
        self.screen.blit(subtitle, subtitle.get_rect(center=(header_box.centerx, header_box.y + 74)))
        self.screen.blit(source, source.get_rect(center=(header_box.centerx, header_box.y + 98)))

        preview_rect = pygame.Rect(content_left, 236, content_width, 290)
        self._draw_highway(preview_rect, preview_time=16.0, show_song_banner=False)
        self._draw_panel(selector_box)

        selector_title = self.ui_font.render("Canciones", True, HUD_TEXT)
        self.screen.blit(selector_title, (selector_box.x + 18, selector_box.y + 16))

        list_start = max(0, self.selected_song_index - 3)
        visible_songs = self.song_library[list_start:list_start + 8]
        for relative_index, song in enumerate(visible_songs):
            actual_index = list_start + relative_index
            item_y = selector_box.y + 58 + (relative_index * 24)
            is_selected = actual_index == self.selected_song_index
            if is_selected:
                highlight = pygame.Rect(selector_box.x + 10, item_y - 2, selector_box.width - 20, 22)
                pygame.draw.rect(self.screen, (64, 62, 44), highlight, border_radius=10)

            text = f"{song.artist} - {song.title}"
            text_surface = self.tiny_font.render(text[:40], True, HUD_TEXT)
            self.screen.blit(text_surface, (selector_box.x + 20, item_y))

        footer_box = pygame.Rect(28, WINDOW_HEIGHT - 186, WINDOW_WIDTH - 56, 118)
        self._draw_panel(footer_box, fill_alpha=105, radius=20)
        self.screen.blit(hint, hint.get_rect(center=(footer_box.centerx, footer_box.y + 28)))
        nav_hint = self.small_font.render("Flechas arriba/abajo para elegir cancion", True, HUD_TEXT)
        self.screen.blit(nav_hint, nav_hint.get_rect(center=(footer_box.centerx, footer_box.y + 56)))
        self.screen.blit(difficulty_hint, difficulty_hint.get_rect(center=(footer_box.centerx, footer_box.y + 80)))
        compact_info = self.small_font.render(
            f"Notas: {len(self.notes)}  |  Voz: siguiente, atras, volver, facil, medio, dificil",
            True,
            HUD_TEXT,
        )
        self.screen.blit(compact_info, compact_info.get_rect(center=(footer_box.centerx, footer_box.y + 102)))

    def _draw_playfield(self):
        highway_width = min(900, max(760, int(WINDOW_WIDTH * 0.86)))
        highway_height = min(570, max(500, int(WINDOW_HEIGHT * 0.60)))
        highway_rect = pygame.Rect(0, 0, highway_width, highway_height)
        highway_rect.center = (WINDOW_WIDTH // 2 + 8, int(WINDOW_HEIGHT * 0.43))
        self._draw_highway(highway_rect, self._current_song_time(), show_song_banner=True)
        self._draw_hud(highway_rect)

    def _draw_results(self):
        overlay = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 150))
        self.screen.blit(overlay, (0, 0))

        box = pygame.Rect(0, 0, 500, 230)
        box.center = (WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2)
        pygame.draw.rect(self.screen, (18, 18, 22), box, border_radius=22)
        pygame.draw.rect(self.screen, (240, 215, 120), box, 3, border_radius=22)

        title = self.title_font.render("Cancion terminada", True, HUD_TEXT)
        score = self.ui_font.render(f"Puntaje: {self.score}", True, HUD_TEXT)
        combo = self.ui_font.render(f"Mejor combo: {self.best_combo}", True, HUD_TEXT)
        hint = self.small_font.render("ENTER para volver al inicio", True, HUD_TEXT)

        self.screen.blit(title, title.get_rect(center=(box.centerx, box.y + 54)))
        self.screen.blit(score, score.get_rect(center=(box.centerx, box.y + 115)))
        self.screen.blit(combo, combo.get_rect(center=(box.centerx, box.y + 155)))
        self.screen.blit(hint, hint.get_rect(center=(box.centerx, box.y + 193)))

    def _draw_failed_overlay(self):
        overlay = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
        overlay.fill((20, 0, 0, 148))
        self.screen.blit(overlay, (0, 0))

        box = pygame.Rect(0, 0, 540, 240)
        box.center = (WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2)
        pygame.draw.rect(self.screen, (28, 10, 10), box, border_radius=22)
        pygame.draw.rect(self.screen, (255, 90, 90), box, 3, border_radius=22)

        title = self.title_font.render("Perdiste", True, (255, 228, 228))
        reason = self.ui_font.render(self.failed_message, True, (255, 176, 176))
        combo = self.ui_font.render(f"Mejor combo: {self.best_combo}", True, HUD_TEXT)
        hint = self.small_font.render("ENTER para volver al inicio", True, HUD_TEXT)

        self.screen.blit(title, title.get_rect(center=(box.centerx, box.y + 56)))
        self.screen.blit(reason, reason.get_rect(center=(box.centerx, box.y + 112)))
        self.screen.blit(combo, combo.get_rect(center=(box.centerx, box.y + 156)))
        self.screen.blit(hint, hint.get_rect(center=(box.centerx, box.y + 196)))

    def _draw_pause_overlay(self):
        overlay = pygame.Surface((WINDOW_WIDTH, WINDOW_HEIGHT), pygame.SRCALPHA)
        overlay.fill((0, 0, 0, 120))
        self.screen.blit(overlay, (0, 0))

        box = pygame.Rect(0, 0, 420, 290)
        box.center = (WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2)
        self._draw_panel(box, border_color=(255, 230, 120), fill_alpha=145, radius=22)

        title = self.title_font.render("Menu", True, HUD_TEXT)
        self.screen.blit(title, title.get_rect(center=(box.centerx, box.y + 38)))

        for index, option in enumerate(self.pause_menu_options):
            option_rect = pygame.Rect(box.x + 32, box.y + 80 + (index * 46), box.width - 64, 36)
            if index == self.pause_menu_index:
                pygame.draw.rect(self.screen, (88, 76, 32), option_rect, border_radius=12)
            option_surface = self.ui_font.render(option, True, HUD_TEXT)
            self.screen.blit(option_surface, option_surface.get_rect(center=option_rect.center))

    def _draw_pause_difficulty_overlay(self):
        box = pygame.Rect(0, 0, 320, 238)
        box.center = (WINDOW_WIDTH // 2 + 250, WINDOW_HEIGHT // 2)
        self._draw_panel(box, border_color=(180, 220, 255), fill_alpha=150, radius=18)
        title = self.ui_font.render("Cambiar nivel", True, HUD_TEXT)
        self.screen.blit(title, title.get_rect(center=(box.centerx, box.y + 28)))

        for index, option in enumerate(self.pause_difficulty_options):
            option_rect = pygame.Rect(box.x + 22, box.y + 60 + (index * 40), box.width - 44, 30)
            if index == self.pause_difficulty_index:
                pygame.draw.rect(self.screen, (42, 66, 92), option_rect, border_radius=10)
            option_surface = self.small_font.render(option, True, HUD_TEXT)
            self.screen.blit(option_surface, option_surface.get_rect(center=option_rect.center))

    def _draw_confirm_overlay(self):
        box = pygame.Rect(0, 0, 380, 180)
        box.center = (WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 + 120)
        self._draw_panel(box, border_color=(255, 180, 120), fill_alpha=152, radius=18)
        title = self.ui_font.render("Estas seguro?", True, HUD_TEXT)
        self.screen.blit(title, title.get_rect(center=(box.centerx, box.y + 42)))

        for index, option in enumerate(self.confirm_options):
            option_rect = pygame.Rect(box.x + 54 + (index * 140), box.y + 92, 90, 40)
            if index == self.confirm_index:
                pygame.draw.rect(self.screen, (92, 60, 32), option_rect, border_radius=12)
            option_surface = self.ui_font.render(option, True, HUD_TEXT)
            self.screen.blit(option_surface, option_surface.get_rect(center=option_rect.center))

    def _draw_credits(self):
        title = self.title_font.render("Creditos", True, HUD_TEXT)
        box = pygame.Rect(0, 0, 760, 360)
        box.center = (WINDOW_WIDTH // 2, WINDOW_HEIGHT // 2 - 30)
        self._draw_panel(box, fill_alpha=120, radius=24)
        self.screen.blit(title, title.get_rect(center=(box.centerx, box.y + 46)))

        for index, line in enumerate(CREDIT_LINES):
            surface = self.ui_font.render(line[:56], True, HUD_TEXT)
            self.screen.blit(surface, surface.get_rect(center=(box.centerx, box.y + 112 + (index * 48))))

        hint = self.small_font.render("Di o escribe volver para regresar al menu principal", True, HUD_TEXT)
        self.screen.blit(hint, hint.get_rect(center=(box.centerx, box.bottom - 34)))

    def _draw_command_bar(self):
        if not self._supports_command_input():
            return

        bar_rect = pygame.Rect(24, WINDOW_HEIGHT - 54, WINDOW_WIDTH - 48, 34)
        self._draw_panel(bar_rect, fill_alpha=112, radius=18)

        prompt = self.small_font.render(f"Comandos ({self.voice_backend_name}):", True, HUD_TEXT)
        command_text = self.tiny_font.render(self.command_buffer or "...", True, HUD_TEXT)
        feedback = self.tiny_font.render(self.command_feedback[:120], True, HUD_TEXT)

        self.screen.blit(prompt, (bar_rect.x + 12, bar_rect.y + 3))
        self.screen.blit(command_text, (bar_rect.x + 190, bar_rect.y + 5))
        self.screen.blit(feedback, (bar_rect.x + 12, bar_rect.y + 18))

    def _draw_hud(self, highway_rect: pygame.Rect):
        left_panel = pygame.Rect(18, 18, 248, 220)
        self._draw_panel(left_panel, fill_alpha=110, radius=20)

        score_surface = self.ui_font.render(f"Score {self.score}", True, HUD_TEXT)
        combo_surface = self.ui_font.render(f"Combo x{self.combo}", True, HUD_TEXT)
        difficulty_surface = self.small_font.render(
            f"Dificultad: {DIFFICULTY_PROFILES[self.selected_difficulty].label}",
            True,
            HUD_TEXT,
        )
        artist_surface = self.small_font.render(self.song_data.artist, True, HUD_TEXT)
        song_surface = self.small_font.render(self.song_data.title, True, HUD_TEXT)

        judgement_color = HUD_TEXT if self.last_judgement != "Miss" else MISS_TEXT
        judgement_surface = self.ui_font.render(self.last_judgement, True, judgement_color)
        streak_color = MISS_TEXT if self.miss_streak >= max(1, self._current_profile().fail_streak - 2) else HUD_TEXT
        streak_surface = self.small_font.render(f"Fallos seguidos: {self.miss_streak}", True, streak_color)

        self.screen.blit(score_surface, (32, 32))
        self.screen.blit(combo_surface, (32, 66))
        self.screen.blit(judgement_surface, (32, 104))
        self.screen.blit(difficulty_surface, (32, 142))
        self.screen.blit(streak_surface, (32, 164))
        self.screen.blit(song_surface, (32, 186))
        self.screen.blit(artist_surface, (32, 208))

        self._draw_health_meter(highway_rect)

        bottom_panel = pygame.Rect(max(18, WINDOW_WIDTH - 320), WINDOW_HEIGHT - 118, 300, 30)
        self._draw_panel(bottom_panel, fill_alpha=96, radius=14)
        port_surface = self.small_font.render("Recibiendo golpes UDP 5053", True, HUD_TEXT)
        self.screen.blit(port_surface, (bottom_panel.x + 12, bottom_panel.y + 6))

    def _draw_health_meter(self, highway_rect: pygame.Rect):
        meter_height = min(250, max(190, int(highway_rect.height * 0.42)))
        shell_rect = pygame.Rect(12, 270, 42, meter_height)
        shell_points = [
            (shell_rect.x + 12, shell_rect.y),
            (shell_rect.right, shell_rect.y + 10),
            (shell_rect.right, shell_rect.bottom - 8),
            (shell_rect.x + 5, shell_rect.bottom),
            (shell_rect.x, shell_rect.bottom - 12),
            (shell_rect.x, shell_rect.y + 10),
        ]
        shadow = pygame.Surface((shell_rect.width + 22, shell_rect.height + 22), pygame.SRCALPHA)
        shifted_points = [(x - shell_rect.x + 11, y - shell_rect.y + 11) for x, y in shell_points]
        pygame.draw.polygon(shadow, (0, 0, 0, 85), shifted_points)
        self.screen.blit(shadow, (shell_rect.x - 11, shell_rect.y + 8))

        pygame.draw.polygon(self.screen, (26, 28, 32), shell_points)
        pygame.draw.polygon(self.screen, (230, 230, 230), shell_points, 3)

        meter_rect = pygame.Rect(shell_rect.x + 10, shell_rect.y + 16, 16, shell_rect.height - 30)

        color_sections = [
            ((70, 210, 110), 0.0, 0.34),
            ((240, 202, 72), 0.34, 0.68),
            ((224, 66, 66), 0.68, 1.0),
        ]
        for color, start_ratio, end_ratio in color_sections:
            top = meter_rect.y + int(meter_rect.height * start_ratio)
            height = max(1, int(meter_rect.height * (end_ratio - start_ratio)))
            pygame.draw.rect(self.screen, color, pygame.Rect(meter_rect.x, top, meter_rect.width, height))

        fill_height = int(meter_rect.height * self.display_health)
        empty_height = meter_rect.height - fill_height
        if empty_height > 0:
            empty_rect = pygame.Rect(meter_rect.x, meter_rect.y, meter_rect.width, empty_height)
            pygame.draw.rect(self.screen, (14, 14, 18), empty_rect)

        pygame.draw.rect(self.screen, (245, 240, 228), meter_rect, 2)
        glow_height = max(8, min(meter_rect.height, fill_height))
        glow_rect = pygame.Rect(meter_rect.x - 8, meter_rect.bottom - glow_height, meter_rect.width + 16, glow_height)
        glow = pygame.Surface((glow_rect.width, glow_rect.height), pygame.SRCALPHA)
        pygame.draw.rect(glow, (255, 245, 205, 42), glow.get_rect())
        self.screen.blit(glow, glow_rect.topleft)

        marker_y = meter_rect.bottom - int(meter_rect.height * self.display_health)
        pygame.draw.line(
            self.screen,
            (255, 255, 255),
            (meter_rect.x - 4, marker_y),
            (meter_rect.right + 4, marker_y),
            3,
        )

        label = self.small_font.render("Vida", True, HUD_TEXT)
        self.screen.blit(label, label.get_rect(center=(shell_rect.centerx + 2, shell_rect.y - 14)))

    def _draw_highway(self, rect: pygame.Rect, preview_time: float, show_song_banner: bool):
        top_width = rect.width * 0.36
        bottom_width = rect.width * 0.98
        top_center_x = rect.centerx
        top_y = rect.y + 12
        bottom_y = rect.bottom - 86
        strike_y = rect.bottom - 100 # Antes: - 130
        kick_y = rect.bottom - 100   # Antes: - 58 (ahora son iguales)

        failure_progress = 0.0
        if self.state == "failed" and self.failure_started_at is not None:
            elapsed = (pygame.time.get_ticks() / 1000.0) - self.failure_started_at
            failure_progress = max(0.0, min(1.0, elapsed / 1.2))
            shake = math.sin(elapsed * 38.0) * (18 * (1.0 - failure_progress))
            top_center_x += shake

        left_top = (top_center_x - (top_width / 2), top_y)
        right_top = (top_center_x + (top_width / 2), top_y)
        left_bottom = (rect.centerx - (bottom_width / 2), bottom_y)
        right_bottom = (rect.centerx + (bottom_width / 2), bottom_y)

        self.glow_surface.fill((0, 0, 0, 0)) 
        pygame.draw.polygon(
            self.glow_surface,
            (255, 190, 90, 28),
            [
                (40 + left_top[0] - rect.x, 30 + left_top[1] - rect.y),
                (40 + right_top[0] - rect.x, 30 + right_top[1] - rect.y),
                (40 + right_bottom[0] - rect.x, 30 + right_bottom[1] - rect.y),
                (40 + left_bottom[0] - rect.x, 30 + left_bottom[1] - rect.y),
            ],
        )
        self.screen.blit(self.glow_surface, (rect.x - 40, rect.y - 30))

        # Pinta la carretera negra principal
        pygame.draw.polygon(self.screen, HIGHWAY_FILL, [left_top, right_top, right_bottom, left_bottom])
        pygame.draw.polygon(self.screen, HIGHWAY_EDGE, [left_top, right_top, right_bottom, left_bottom], 4)

        if failure_progress > 0.0:
            if self.crack_overlay is None:
                self.crack_overlay = pygame.Surface((rect.width + 30, rect.height + 20), pygame.SRCALPHA)
            else:
                self.crack_overlay.fill((0, 0, 0, 0))
                
            crack_alpha = int(140 * failure_progress)
            crack_lines = [
                ((60, 120), (220, 240), (160, 340), (310, 490)),
                ((rect.width - 100, 80), (rect.width - 210, 240), (rect.width - 160, 360), (rect.width - 280, 510)),
                ((rect.width // 2, 40), (rect.width // 2 - 40, 190), (rect.width // 2 + 25, 330), (rect.width // 2 - 18, 500)),
            ]
            for line_points in crack_lines:
                pygame.draw.lines(self.crack_overlay, (255, 230, 230, crack_alpha), False, line_points, 3)
                for point in line_points[1:-1]:
                    branch = [point, (point[0] + 28, point[1] + 26)]
                    pygame.draw.lines(self.crack_overlay, (255, 130, 130, crack_alpha), False, branch, 2)
            self.screen.blit(self.crack_overlay, (rect.x - 15, rect.y - 10))

            if self.danger_overlay is None:
                 self.danger_overlay = pygame.Surface((rect.width, rect.height), pygame.SRCALPHA)
                 
            self.danger_overlay.fill((0, 0, 0, 0))
            pygame.draw.polygon(
                self.danger_overlay,
                (255, 40, 40, int(58 * failure_progress)),
                [(0, 0), (rect.width, 0), (rect.width, rect.height), (0, rect.height)],
            )
            self.screen.blit(self.danger_overlay, rect.topleft)

        # 2. Las sub-superficies para los carriles han sido reemplazadas por llamadas 
        #    a dibujados directos en pantalla mediante un Surface preexistente o usando gfxdraw/draw si es posible.
        #    Pintaremos una Surface compartida
        self.blank_surface.fill((0, 0, 0, 0))

        for lane_index, color in enumerate(LANE_COLORS):
            left_ratio = lane_index / 5
            right_ratio = (lane_index + 1) / 5
            lane_poly = [
                self._point_between(left_top, right_top, left_ratio),
                self._point_between(left_top, right_top, right_ratio),
                self._point_between(left_bottom, right_bottom, right_ratio),
                self._point_between(left_bottom, right_bottom, left_ratio),
            ]
            shifted = [(point[0] - rect.x, point[1] - rect.y) for point in lane_poly]
            pygame.draw.polygon(self.blank_surface, (*color, 18), shifted)
            
        self.screen.blit(self.blank_surface, (rect.x, rect.y))

        for grid_index in range(11):
            progress = grid_index / 10
            y = top_y + ((bottom_y - top_y) * progress)
            left = self._interpolate_point(left_top, left_bottom, progress)
            right = self._interpolate_point(right_top, right_bottom, progress)
            pygame.draw.line(self.screen, (182, 182, 182), left, right, 1)
            if grid_index % 2 == 0:
                pygame.draw.line(self.screen, (70, 70, 70), left, right, 3)

        for lane_boundary in range(1, 5):
            lane_progress = lane_boundary / 5
            top_point = self._interpolate_point(left_top, right_top, lane_progress)
            bottom_point = self._interpolate_point(left_bottom, right_bottom, lane_progress)
            pygame.draw.line(self.screen, (118, 118, 118), top_point, bottom_point, 2)

        strike_left = self._point_on_width(left_top, left_bottom, right_top, right_bottom, strike_y, 0.0)
        strike_right = self._point_on_width(left_top, left_bottom, right_top, right_bottom, strike_y, 1.0)
        
        # 3. Utilizar el 'strike glow' pre-calculado del INIT
        self.screen.blit(self.strike_glow, (strike_left[0] - 10, strike_y - 10))
        pygame.draw.line(self.screen, (235, 235, 235), strike_left, strike_right, 4)

        kick_left = self._point_on_width(left_top, left_bottom, right_top, right_bottom, kick_y, 0.0)
        kick_right = self._point_on_width(left_top, left_bottom, right_top, right_bottom, kick_y, 1.0)
        pygame.draw.line(self.screen, (130, 130, 130), kick_left, kick_right, 10)

        for lane_index, color in enumerate(LANE_COLORS):
            lane_x = self._lane_center_x(left_top, left_bottom, right_top, right_bottom, strike_y, lane_index)
            radius = 28
            is_pressed = self._was_recent_zone_hit(self._lane_zone(lane_index))
            fill = color if is_pressed else (28, 28, 28)
            pygame.draw.circle(self.screen, fill, (int(lane_x), int(strike_y)), radius)
            pygame.draw.circle(self.screen, color, (int(lane_x), int(strike_y)), radius, 5)

        kick_pressed = self._was_recent_zone_hit("bombo")
        active_kick_color = KICK_COLOR_PRESSED if kick_pressed else KICK_COLOR
        pygame.draw.line(self.screen, active_kick_color, kick_left, kick_right, 12)

        for note in self.notes:
            if note.judged and not note.hit:
                continue

            time_until_hit = note.time - preview_time
            if time_until_hit < -LATE_HIT_WINDOW_SECONDS:
                continue  # La nota ya pasó, saltamos a revisar la otra
                
            if time_until_hit > PREVIEW_LEAD_SECONDS:
                break 

            progress = 1.0 - (time_until_hit / PREVIEW_LEAD_SECONDS)
            progress = max(0.0, min(1.0, progress))
            travel_progress = progress ** NOTE_TRAVEL_CURVE

            if note.zone == "bombo":
                y = top_y + ((kick_y - top_y) * travel_progress)
                kick_note_left = self._point_on_width(left_top, left_bottom, right_top, right_bottom, y, 0.0)
                kick_note_right = self._point_on_width(left_top, left_bottom, right_top, right_bottom, y, 1.0)
                color = (255, 255, 255) if not note.hit else (255, 240, 190)
                pygame.draw.line(
                    self.screen,
                    color,
                    kick_note_left,
                    kick_note_right,
                    max(5, int(4 + (progress * 10))),
                )
                continue

            lane_index = ZONE_TO_LANE[note.zone]
            y = top_y + ((strike_y - top_y) * travel_progress)
            lane_x = self._lane_center_x(left_top, left_bottom, right_top, right_bottom, y, lane_index)
            radius = max(12, int(12 + (progress * 18)))
            color = LANE_COLORS[lane_index]
            fill = color if not note.hit else (255, 246, 185)
            
            # --- 4. OPTIMIZACIÓN DE SOMBRA CLONADA DE NOTA
            # Buscar en la cache si ya dibujamos una sombra en la memoria de este exacto tamaño y color
            cache_key = (color, radius)
            if cache_key not in self.note_shadow_surfaces:
                shadow_radius = radius + 6
                shadow_surface = pygame.Surface((shadow_radius * 2 + 8, shadow_radius * 2 + 8), pygame.SRCALPHA)
                pygame.draw.circle(
                    shadow_surface,
                    (*color, 45),
                    (shadow_radius + 4, shadow_radius + 4),
                    shadow_radius,
                )
                self.note_shadow_surfaces[cache_key] = (shadow_surface, shadow_radius)
                
            shadow_surface_cached, shadow_radius_cached = self.note_shadow_surfaces[cache_key]
            
            self.screen.blit(shadow_surface_cached, (int(lane_x) - shadow_radius_cached - 4, int(y) - shadow_radius_cached - 4))
            # ---------------------------------------------
            pygame.draw.circle(self.screen, fill, (int(lane_x), int(y)), radius)
            pygame.draw.circle(self.screen, (240, 240, 240), (int(lane_x), int(y)), radius, 3)

        if show_song_banner:
            banner_width = min(rect.width - 180, WINDOW_WIDTH - 360)
            top_label_panel = pygame.Rect(0, 0, banner_width, 54)
            top_label_panel.centerx = rect.centerx
            top_label_panel.y = max(58, rect.y - 76)
            self._draw_panel(top_label_panel, fill_alpha=102, radius=16)
            self.screen.blit(self.cached_song_label, (top_label_panel.x + 16, top_label_panel.y + 6))
            self.screen.blit(self.cached_legend_label, (top_label_panel.x + 16, top_label_panel.y + 30))

    def _current_song_time(self):
        if self.song_started_at is None:
            return 0.0

        # Calculamos cuánto tiempo ha pasado en total, restándole el tiempo que el juego ha estado en pausa
        paused_time = self.accumulated_pause_seconds
        if self.pause_started_at is not None:
            paused_time += (pygame.time.get_ticks() / 1000.0) - self.pause_started_at

        # El reloj de tu PC (get_ticks) no sufre desincronización
        return (pygame.time.get_ticks() / 1000.0) - self.song_started_at - paused_time - GLOBAL_OFFSET_SECONDS

    def _lane_center_x(self, left_top, left_bottom, right_top, right_bottom, y, lane_index):
        progress = self._vertical_progress(left_top[1], left_bottom[1], y)
        left = self._interpolate_point(left_top, left_bottom, progress)
        right = self._interpolate_point(right_top, right_bottom, progress)
        lane_progress = (lane_index + 0.5) / 5
        return left[0] + ((right[0] - left[0]) * lane_progress)

    def _point_on_width(self, left_top, left_bottom, right_top, right_bottom, y, width_progress):
        progress = self._vertical_progress(left_top[1], left_bottom[1], y)
        left = self._interpolate_point(left_top, left_bottom, progress)
        right = self._interpolate_point(right_top, right_bottom, progress)
        return (
            int(left[0] + ((right[0] - left[0]) * width_progress)),
            int(y),
        )

    def _vertical_progress(self, top_y, bottom_y, y):
        if bottom_y == top_y:
            return 0.0
        return max(0.0, min(1.0, (y - top_y) / (bottom_y - top_y)))

    def _interpolate_point(self, start, end, amount):
        return (
            int(start[0] + ((end[0] - start[0]) * amount)),
            int(start[1] + ((end[1] - start[1]) * amount)),
        )

    def _point_between(self, start, end, amount):
        return (
            int(start[0] + ((end[0] - start[0]) * amount)),
            int(start[1] + ((end[1] - start[1]) * amount)),
        )

    def _lane_zone(self, lane_index):
        for zone, index in ZONE_TO_LANE.items():
            if index == lane_index:
                return zone
        return ""

    def _was_recent_zone_hit(self, zone):
        current_time = pygame.time.get_ticks() / 1000.0
        return self.last_hit_zone == zone and (current_time - self.last_hit_at) < 0.18


def main():
    game = RhythmGame()
    game.run()


if __name__ == "__main__":
    main()
