import json
import socket
import time

import speech_recognition as sr


VOICE_UDP_HOST = "127.0.0.1"
VOICE_UDP_PORT = 5054
VOICE_MIC_DEVICE_INDEX = None  # None = micrófono por defecto


def enviar_comando(texto: str):
    payload = {"command": texto}
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.sendto(json.dumps(payload).encode("utf-8"), (VOICE_UDP_HOST, VOICE_UDP_PORT))


def callback(recognizer, audio):
    try:
        texto = recognizer.recognize_google(audio, language="es-ES").lower().strip()
        if not texto:
            return

        print(f"Texto reconocido: {texto}")
        enviar_comando(texto)
    except sr.UnknownValueError:
        pass
    except sr.RequestError:
        print("Error de conexion con reconocimiento de voz.")


def iniciar_sistema():
    recognizer = sr.Recognizer()

    try:
        microphone = sr.Microphone(device_index=VOICE_MIC_DEVICE_INDEX) if VOICE_MIC_DEVICE_INDEX is not None else sr.Microphone()
    except OSError:
        print("Dispositivo de micrófono específico no encontrado. Usando micrófono por defecto.")
        microphone = sr.Microphone()

    with microphone as source:
        print("Calibrando microfono... Silencio, por favor.")
        recognizer.adjust_for_ambient_noise(source, duration=1.5)
        recognizer.energy_threshold = 150
        recognizer.dynamic_energy_threshold = True
        recognizer.pause_threshold = 0.35
        recognizer.non_speaking_duration = 0.2

    print("Sistema listo. Di comandos como menu, pausa, jugar o siguiente.")
    stop_listening = recognizer.listen_in_background(microphone, callback, phrase_time_limit=1.5)

    try:
        while True:
            time.sleep(0.25)
    except KeyboardInterrupt:
        stop_listening(wait_for_stop=False)
        print("\nApagando listener de voz...")


if __name__ == "__main__":
    iniciar_sistema()
