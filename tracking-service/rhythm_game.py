"""Juego rítmico de batería (Drum Hero) con lectura de charts MIDI y playback de stems.

Este módulo carga canciones desde assets/songs, interpreta PART DRUMS del MIDI
y sincroniza audio (song/vocals/rhythm/drums_1..4) con el gameplay.
"""

import configparser
import json
import math
import socket
from bisect import bisect_right
from dataclasses import dataclass
from pathlib import Path

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
    notes: list[Note]
    length_seconds: float
    source_name: str
    inferred_kick: bool


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
        midi_notes, chart_length, source_name, inferred_kick = self._read_chart(midi_path)

        if not midi_notes:
            return None

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
            notes=midi_notes,
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
            notes = self._simplify_for_drums(notes)
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

    def _simplify_for_drums(self, notes):
        """Filtra notas muy densas para que el chart sea tocable en batería."""
        reduced = []
        last_global_time = -999.0
        last_zone_times = {}
        last_non_kick_time = -999.0
        recent_times = []

        for note in notes:
            min_gap = DRUM_FRIENDLY_KICK_GAP_SECONDS if note.zone == "bombo" else DRUM_FRIENDLY_NOTE_GAP_SECONDS
            last_zone_time = last_zone_times.get(note.zone, -999.0)

            if note.time - last_zone_time < min_gap:
                continue

            if note.zone != "bombo" and note.time - last_non_kick_time < DRUM_CHORD_WINDOW_SECONDS:
                continue

            if note.zone != "bombo" and note.time - last_global_time < DRUM_GLOBAL_MIN_GAP_SECONDS:
                continue

            # Global density cap: allow at most DRUM_MAX_NOTES_PER_SECOND hits in the last second
            recent_times = [t for t in recent_times if note.time - t <= 1.0]
            if len(recent_times) >= DRUM_MAX_NOTES_PER_SECOND:
                continue

            reduced.append(note)
            last_zone_times[note.zone] = note.time
            last_global_time = note.time
            if note.zone != "bombo":
                last_non_kick_time = note.time
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
        notes = self._simplify_for_drums(notes)

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
        pygame.mixer.pre_init(44100, -16, 2, 512)
        pygame.init()
        pygame.mixer.init()
        pygame.mixer.set_num_channels(8)
        self.screen = pygame.display.set_mode((WINDOW_WIDTH, WINDOW_HEIGHT))
        pygame.display.set_caption(APP_TITLE)
        self.clock = pygame.time.Clock()

        self.title_font = pygame.font.SysFont("arial", 42, bold=True)
        self.ui_font = pygame.font.SysFont("arial", 24, bold=True)
        self.small_font = pygame.font.SysFont("arial", 18)
        self.tiny_font = pygame.font.SysFont("arial", 16)

        self.receiver = UdpHitReceiver(UDP_HOST, UDP_PORT)
        self.song_library = SongLoader().load_all_songs()
        self.selected_song_index = 0
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

        self.state = "intro"
        self.song_started_at = None
        self.music_started = False
        self.running = True

        self.score = 0
        self.combo = 0
        self.best_combo = 0
        self.last_hit_zone = None
        self.last_hit_at = -999.0
        self.last_judgement = "Listo para tocar"

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
            self.receiver.close()
            pygame.quit()

    def _load_cover_surface(self, cover_path: Path | None):
        if cover_path is None or not cover_path.exists():
            return None

        try:
            surface = pygame.image.load(str(cover_path)).convert_alpha()
        except pygame.error:
            return None

        return pygame.transform.smoothscale(surface, (220, 220))

    def _handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
                return

            if event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    self.running = False
                    return

                if self.state == "intro" and event.key == pygame.K_DOWN:
                    self._apply_song_selection(self.selected_song_index + 1)
                    continue

                if self.state == "intro" and event.key == pygame.K_UP:
                    self._apply_song_selection(self.selected_song_index - 1)
                    continue

                if self.state == "intro" and event.key in (pygame.K_RETURN, pygame.K_SPACE):
                    self._start_song()
                    continue

                if self.state == "finished" and event.key == pygame.K_RETURN:
                    self._restart_song()
                    continue

                zone = KEYBOARD_ZONE_MAP.get(event.key)
                if zone and self.state == "playing":
                    self._register_hit(zone)

    def _update(self, _dt: float):
        current_time = pygame.time.get_ticks() / 1000.0

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

    def _start_song(self):
        # --- PRECARGAR AUDIO ANTES DE INICIAR EL TIEMPO ---
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
        self.score = 0
        self.combo = 0
        self.best_combo = 0
        self.last_hit_zone = None
        self.last_hit_at = -999.0
        self.last_judgement = "Comienza"

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
        self.state = "intro"
        self.song_started_at = None
        self.music_started = False

    def _apply_song_selection(self, index: int):
        if not self.song_library:
            return

        self.selected_song_index = index % len(self.song_library)
        self.song_data = self.song_library[self.selected_song_index]
        self.notes = self.song_data.notes
        self.song_length = self.song_data.length_seconds
        self.cover_surface = self._load_cover_surface(self.song_data.cover_path)
        # Defer loading stems until playback to avoid blocking UI on selection
        self.vocals_sound = None
        self.rhythm_sound = None
        self.drums_sounds = []

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
        if self.song_started_at is None:
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
            self.last_judgement = "Muy pronto" if self._has_upcoming_note(zone, song_time) else "Fuera de tiempo"
            self.last_hit_zone = zone
            self.last_hit_at = current_time
            return

        candidate.hit = True
        candidate.judged = True
        self.combo += 1
        self.best_combo = max(self.best_combo, self.combo)
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
                self.last_judgement = "Miss"

    def _draw(self):
        self._draw_background()

        if self.state == "intro":
            self._draw_intro()
        elif self.state == "playing":
            self._draw_playfield()
        elif self.state == "finished":
            self._draw_playfield()
            self._draw_results()

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

    def _draw_intro(self):
        title = self.title_font.render("AirDrums Hero", True, HUD_TEXT)
        subtitle = self.ui_font.render(f"{self.song_data.artist} - {self.song_data.title}", True, HUD_TEXT)
        hint = self.ui_font.render("ENTER para empezar", True, HUD_TEXT)
        source = self.small_font.render(f"Chart: {self.song_data.source_name}", True, HUD_TEXT)
        kick_text = "Usando chart PART DRUMS (bateria)"
        kick_hint = self.small_font.render(kick_text, True, HUD_TEXT)

        controls = self.small_font.render(
            "Prueba manual: A platillo, S hi-hat, D tarola, J tom sup, K tom inf, SPACE bombo",
            True,
            HUD_TEXT,
        )
        udp_hint = self.small_font.render(
            "Tambien escucha golpes reales desde middleware por UDP 127.0.0.1:5053",
            True,
            HUD_TEXT,
        )

        header_box = pygame.Rect(340, 58, 580, 132)
        self._draw_panel(header_box, fill_alpha=110, radius=24)
        self.screen.blit(title, title.get_rect(center=(header_box.centerx, header_box.y + 40)))
        self.screen.blit(subtitle, subtitle.get_rect(center=(header_box.centerx, header_box.y + 83)))
        self.screen.blit(source, source.get_rect(center=(header_box.centerx, header_box.y + 112)))

        preview_rect = pygame.Rect(0, 0, 760, 300)
        preview_rect.center = (WINDOW_WIDTH // 2 + 150, 372)
        self._draw_highway(preview_rect, preview_time=16.0, show_song_banner=False)

        selector_box = pygame.Rect(66, 274, 360, 260)
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

        footer_box = pygame.Rect(220, 540, 840, 150)
        self._draw_panel(footer_box, fill_alpha=105, radius=20)
        self.screen.blit(hint, hint.get_rect(center=(footer_box.centerx, footer_box.y + 28)))
        nav_hint = self.small_font.render("Flechas arriba/abajo para elegir cancion", True, HUD_TEXT)
        self.screen.blit(nav_hint, nav_hint.get_rect(center=(footer_box.centerx, footer_box.y + 58)))
        self.screen.blit(kick_hint, kick_hint.get_rect(center=(footer_box.centerx, footer_box.y + 80)))
        self.screen.blit(controls, controls.get_rect(center=(footer_box.centerx, footer_box.y + 108)))
        self.screen.blit(udp_hint, udp_hint.get_rect(center=(footer_box.centerx, footer_box.y + 132)))

    def _draw_playfield(self):
        highway_rect = pygame.Rect(0, 0, 900, 570)
        highway_rect.center = (WINDOW_WIDTH // 2 + 30, 408)
        self._draw_highway(highway_rect, self._current_song_time(), show_song_banner=True)
        self._draw_hud()

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

    def _draw_hud(self):
        left_panel = pygame.Rect(18, 18, 245, 180)
        self._draw_panel(left_panel, fill_alpha=110, radius=20)

        score_surface = self.ui_font.render(f"Score {self.score}", True, HUD_TEXT)
        combo_surface = self.ui_font.render(f"Combo x{self.combo}", True, HUD_TEXT)
        artist_surface = self.small_font.render(self.song_data.artist, True, HUD_TEXT)
        song_surface = self.small_font.render(self.song_data.title, True, HUD_TEXT)

        judgement_color = HUD_TEXT if self.last_judgement != "Miss" else MISS_TEXT
        judgement_surface = self.ui_font.render(self.last_judgement, True, judgement_color)

        self.screen.blit(score_surface, (32, 32))
        self.screen.blit(combo_surface, (32, 66))
        self.screen.blit(judgement_surface, (32, 104))
        self.screen.blit(song_surface, (32, 145))
        self.screen.blit(artist_surface, (32, 167))

        bottom_panel = pygame.Rect(915, 674, 340, 30)
        self._draw_panel(bottom_panel, fill_alpha=96, radius=14)
        port_surface = self.small_font.render("Recibiendo golpes UDP 5053", True, HUD_TEXT)
        self.screen.blit(port_surface, (930, 680))

    def _draw_highway(self, rect: pygame.Rect, preview_time: float, show_song_banner: bool):
        top_width = rect.width * 0.36
        bottom_width = rect.width * 0.98
        top_center_x = rect.centerx
        top_y = rect.y + 12
        bottom_y = rect.bottom - 86
        strike_y = rect.bottom - 100 # Antes: - 130
        kick_y = rect.bottom - 100   # Antes: - 58 (ahora son iguales)

        left_top = (top_center_x - (top_width / 2), top_y)
        right_top = (top_center_x + (top_width / 2), top_y)
        left_bottom = (rect.centerx - (bottom_width / 2), bottom_y)
        right_bottom = (rect.centerx + (bottom_width / 2), bottom_y)

        glow_surface = pygame.Surface((rect.width + 80, rect.height + 110), pygame.SRCALPHA)
        pygame.draw.polygon(
            glow_surface,
            (255, 190, 90, 28),
            [
                (40 + left_top[0] - rect.x, 30 + left_top[1] - rect.y),
                (40 + right_top[0] - rect.x, 30 + right_top[1] - rect.y),
                (40 + right_bottom[0] - rect.x, 30 + right_bottom[1] - rect.y),
                (40 + left_bottom[0] - rect.x, 30 + left_bottom[1] - rect.y),
            ],
        )
        self.screen.blit(glow_surface, (rect.x - 40, rect.y - 30))

        pygame.draw.polygon(self.screen, HIGHWAY_FILL, [left_top, right_top, right_bottom, left_bottom])
        pygame.draw.polygon(self.screen, HIGHWAY_EDGE, [left_top, right_top, right_bottom, left_bottom], 4)

        for lane_index, color in enumerate(LANE_COLORS):
            left_ratio = lane_index / 5
            right_ratio = (lane_index + 1) / 5
            lane_poly = [
                self._point_between(left_top, right_top, left_ratio),
                self._point_between(left_top, right_top, right_ratio),
                self._point_between(left_bottom, right_bottom, right_ratio),
                self._point_between(left_bottom, right_bottom, left_ratio),
            ]
            lane_surface = pygame.Surface((rect.width + 40, rect.height + 40), pygame.SRCALPHA)
            shifted = [(20 + point[0] - rect.x, 20 + point[1] - rect.y) for point in lane_poly]
            pygame.draw.polygon(lane_surface, (*color, 18), shifted)
            self.screen.blit(lane_surface, (rect.x - 20, rect.y - 20))

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
        strike_glow = pygame.Surface((rect.width + 20, 26), pygame.SRCALPHA)
        pygame.draw.rect(strike_glow, (255, 248, 212, 90), strike_glow.get_rect(), border_radius=10)
        self.screen.blit(strike_glow, (strike_left[0] - 10, strike_y - 10))
        pygame.draw.line(self.screen, (235, 235, 235), strike_left, strike_right, 4)

        kick_left = self._point_on_width(left_top, left_bottom, right_top, right_bottom, kick_y, 0.0)
        kick_right = self._point_on_width(left_top, left_bottom, right_top, right_bottom, kick_y, 1.0)
        pygame.draw.line(self.screen, (130, 130, 130), kick_left, kick_right, 10)

        for lane_index, color in enumerate(LANE_COLORS):
            lane_x = self._lane_center_x(left_top, left_bottom, right_top, right_bottom, strike_y, lane_index)
            radius = 28
            is_pressed = self._was_recent_zone_hit(self._lane_zone(lane_index))
            fill = color if is_pressed else (28, 28, 28)
            # Se eliminó el + 42 para alinear perfectamente con el bombo y la zona de hit
            pygame.draw.circle(self.screen, fill, (int(lane_x), int(strike_y)), radius)
            pygame.draw.circle(self.screen, color, (int(lane_x), int(strike_y)), radius, 5)

        kick_pressed = self._was_recent_zone_hit("bombo")
        active_kick_color = KICK_COLOR_PRESSED if kick_pressed else KICK_COLOR
        pygame.draw.line(self.screen, active_kick_color, kick_left, kick_right, 12)

        for note in self.notes:
            if note.judged and not note.hit:
                continue

            time_until_hit = note.time - preview_time
            if time_until_hit < -LATE_HIT_WINDOW_SECONDS or time_until_hit > PREVIEW_LEAD_SECONDS:
                continue

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
            shadow_radius = radius + 6
            shadow_surface = pygame.Surface((shadow_radius * 2 + 8, shadow_radius * 2 + 8), pygame.SRCALPHA)
            pygame.draw.circle(
                shadow_surface,
                (*color, 45),
                (shadow_radius + 4, shadow_radius + 4),
                shadow_radius,
            )
            self.screen.blit(shadow_surface, (int(lane_x) - shadow_radius - 4, int(y) - shadow_radius - 4))
            pygame.draw.circle(self.screen, fill, (int(lane_x), int(y)), radius)
            pygame.draw.circle(self.screen, (240, 240, 240), (int(lane_x), int(y)), radius, 3)

        if show_song_banner:
            top_label_panel = pygame.Rect(rect.x + 120, rect.y - 64, 520, 54)
            self._draw_panel(top_label_panel, fill_alpha=102, radius=16)
            song_label = self.ui_font.render(f"{self.song_data.artist} - {self.song_data.title}", True, HUD_TEXT)
            legend = self.small_font.render(
                "Platillo | Hi-Hat | Tarola | Tom superior | Tom inferior | Bombo",
                True,
                HUD_TEXT,
            )
            self.screen.blit(song_label, (top_label_panel.x + 16, top_label_panel.y + 6))
            self.screen.blit(legend, (top_label_panel.x + 16, top_label_panel.y + 30))

    def _current_song_time(self):
        if self.song_started_at is None:
            return 0.0
        # Cambiar el return agregando la resta del offset:
        return (pygame.time.get_ticks() / 1000.0) - self.song_started_at - GLOBAL_OFFSET_SECONDS

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
