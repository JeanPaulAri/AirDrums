import socket
import json
import datetime
UDP_IP = "127.0.0.1"
UDP_PORT = 5005

sock_receptor = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
sock_receptor.bind((UDP_IP, UDP_PORT))
sock_receptor.setblocking(False) # ¡Crucial para que pygame no se congele!
count = 1
# Dentro de tu bucle principal de Pygame:
while True:
    try:
        data, addr = sock_receptor.recvfrom(1024) # Leer datos del buffer
        mensaje = json.loads(data.decode('utf-8'))
        
        if mensaje["tipo"] == "golpe":
            print(f"¡Golpe detectado! en Pad: {mensaje['pad']}")
            # Aquí podrías disparar una animación en Pygame
            
        elif mensaje["tipo"] == "posicion":
            count += 1
            if count % 60 == 0: print(f"{datetime.datetime.now().strftime('%H:%M:%S.%f')} - {mensaje}")
        elif mensaje["tipo"] == "configuracion":
            print(mensaje["elementos"])
            
    except BlockingIOError:
        pass # No hay mensajes nuevos en este fotograma, el juego sigue normal