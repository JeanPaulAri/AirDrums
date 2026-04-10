import cv2
import numpy as np
import pygame
import os

import socket  # NUEVO: Para la comunicación UDP
import json    # NUEVO: Para estructurar los mensajes

# --- CONFIGURACIÓN UDP ---
UDP_IP = "127.0.0.1"  # Localhost (asumiendo que Pygame corre en la misma PC)
UDP_PORT = 5005       # Un puerto libre cualquiera
# Crear el socket UDP
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# Función auxiliar para encontrar el centro (X, Y) del color principal en pantalla
def obtener_centro_global(mask):
    contornos, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contornos:
        # Quedarnos con la mancha de color más grande
        c_max = max(contornos, key=cv2.contourArea)
        if cv2.contourArea(c_max) > 500: # Ignorar manchas muy pequeñas
            M = cv2.moments(c_max)
            if M["m00"] != 0:
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])
                return [cx, cy] # Usamos lista para que sea compatible con JSON
    return None # Si no detecta nada

# --- Optimizar latencia de sonido (Buffer más pequeño) ---
pygame.mixer.pre_init(44100, -16, 2, 512) # Ajusta frecuencias y baja el buffer a 512

# Inicializar pygame mixer para sonidos
pygame.mixer.init()

# Aumentar la cantidad de sonidos simultáneos (De 8 a 32 o 64 canales)
pygame.mixer.set_num_channels(32)

# --- Configuración de Sonidos ---
archivos_sonido = {
    (0, 0): "assets/audio/crash.wav", (0, 1): "assets/audio/tom.wav", (0, 2): "assets/audio/tom.wav",
    (1, 0): "assets/audio/hihat.mp3", (1, 1): "assets/audio/tarola.mp3", (1, 2): "assets/audio/tambor_3.mp3",
    (2, 0): "assets/audio/bombo.mp3", (2, 1): "assets/audio/bombo.mp3", (2, 2): "assets/audio/bombo.mp3"
}

sonidos = {}
for celda, archivo in archivos_sonido.items():
    if os.path.exists(archivo):
        sonidos[celda] = pygame.mixer.Sound(archivo)
    else:
        print(f"Aviso: No se encontró el archivo de audio '{archivo}'. Saltando...")
        sonidos[celda] = None

# --- Inicializar la cámara ---
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)

# Azul
# Rosa basado en RGB(254, 169, 204)
# Rango para atrapar tonos rosados/magenta con variaciones de luz
lower_red = np.array([90, 200, 200])
upper_red = np.array([110, 255, 255])

# Verde basado en RGB(1, 189, 37)
# Rango para atrapar este verde intenso y sus sombras
lower_green = np.array([55, 100, 50])
upper_green = np.array([85, 255, 255])

# --- Rangos de Colores en HSV ---
# ROJO (LED de alta luminosidad)
# Nota: El rojo en OpenCV se encuentra en los dos extremos del espectro (0-10 y 170-179)
# Usaremos el rango principal (0-10). Si tu LED rojo tiene un tono hacia el magenta/carmesí, 
# podrías necesitar usar [170, 150, 200] a [179, 255, 255].
#lower_red = np.array([0, 150, 200])   # Saturación y Brillo mínimos muy altos
#upper_red = np.array([10, 255, 255])

# VERDE (LED de alta luminosidad)
# Rango estrecho para verde puro, exigiendo alta saturación y mucho brillo
#lower_green = np.array([50, 150, 200]) # Saturación y Brillo mínimos muy altos
#upper_green = np.array([80, 255, 255])

# --- Diseño de los cuadrados ---
pads_config = {
    # Platillos y Charles (Arco superior)
    (0, 0): (170, 70, 110, 60),  # Platillo Crash 1 (círculo grande)
    (0, 1): (400, 70, 90, 50),  # Tom Alto 1
    (0, 2): (400, 70, 90, 50),  # Tom Medio 2
    
    # Toms y Caja (Arco central-inferior)
    (1, 0): (190, 200, 110, 30),  # Charles
    (1, 1): (320, 240, 140, 50),  # Caja (Snare)
    (1, 2): (480, 240, 140, 50),  # Platillo Ride
    
    # Base
    (2, 0): (200, 440, 110, 30),  # Bombo
    (2, 1): (400, 450, 110, 30),  # Bombo 
    (2, 2): (560, 430, 110, 30)   # Tom de Piso (Floor Tom)
}

ovales = {}
areas_totales = {}
mascaras_roi = {}

for celda, (cx, cy, ancho, alto) in pads_config.items():
    x1 = cx - (ancho // 2)
    y1 = cy - (alto // 2)
    x2 = cx + (ancho // 2)
    y2 = cy + (alto // 2)
    ovales[celda] = (cx, cy, ancho, alto, x1, y1, x2, y2)
    
    # Crear una máscara elíptica de tamaño exacto a la ROI
    mask = np.zeros((alto, ancho), dtype=np.uint8)
    # Rellenar con un óvalo blanco
    cv2.ellipse(mask, (ancho // 2, alto // 2), (ancho // 2, alto // 2), 0, 0, 360, 255, -1)
    mascaras_roi[celda] = mask
    
    # El área de los píxeles en la máscara servirá como área total
    areas_totales[celda] = cv2.countNonZero(mask)

# Control de estado INDEPENDIENTE para cada color
estado_red = {celda: False for celda in ovales}
estado_verde = {celda: False for celda in ovales}


# Variables de control para la configuración
conteo_intercalado = 0
ultimo_pad_tocado = None
pads_configuracion = [(2, 0), (2, 1)] # Coordenadas de los dos bombos

# Bucle configuracion - posicion de pies
while True:
    ret, frame = cap.read()
    if not ret:
        print("No se pudo acceder a la cámara.")
        break
    frame = cv2.flip(frame, 1)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    # 1. Crear máscaras para Rojo y Verde
    mask_red = cv2.inRange(hsv, lower_red, upper_red)
    mask_green = cv2.inRange(hsv, lower_green, upper_green)
    
    # Filtro morfológico
    kernel = np.ones((5,5), np.uint8)
    mask_red = cv2.morphologyEx(mask_red, cv2.MORPH_OPEN, kernel)
    mask_green = cv2.morphologyEx(mask_green, cv2.MORPH_OPEN, kernel)

    # Solo procesamos los dos pads requeridos para esta configuración
    for celda in pads_configuracion:
        cx, cy, ancho, alto, x1, y1, x2, y2 = ovales[celda]
        
        # Límites seguros
        x1_safe, y1_safe = max(0, x1), max(0, y1)
        x2_safe, y2_safe = min(640, x2), min(480, y2)
        
        # Extraer regiones
        roi_red_rect = mask_red[y1_safe:y2_safe, x1_safe:x2_safe]
        roi_green_rect = mask_green[y1_safe:y2_safe, x1_safe:x2_safe]
        
        mx1, my1 = x1_safe - x1, y1_safe - y1
        mx2, my2 = mx1 + (x2_safe - x1_safe), my1 + (y2_safe - y1_safe)
        mask_oval_safe = mascaras_roi[celda][my1:my2, mx1:mx2]
        
        roi_red = cv2.bitwise_and(roi_red_rect, mask_oval_safe)
        roi_green = cv2.bitwise_and(roi_green_rect, mask_oval_safe)
        
        area_total = cv2.countNonZero(mask_oval_safe)
        if area_total == 0: continue

        porcentaje_red = cv2.countNonZero(roi_red) / area_total
        porcentaje_green = cv2.countNonZero(roi_green) / area_total

        tocado_ahora = False

        # Evaluar impacto ROJO
        if porcentaje_red > 0.005:
            if not estado_red[celda]:
                estado_red[celda] = True
                tocado_ahora = True
        else:
            estado_red[celda] = False

        # Evaluar impacto VERDE
        if porcentaje_green > 0.005:
            if not estado_verde[celda]:
                estado_verde[celda] = True
                tocado_ahora = True
        else:
            estado_verde[celda] = False

        # Lógica de conteo intercalado
        if tocado_ahora:
            # Reproducir el sonido correspondiente (opcional en calibración)
            if sonidos[celda]: sonidos[celda].play()

            if ultimo_pad_tocado is None:
                # Es el primer bombo que toca
                ultimo_pad_tocado = celda
                conteo_intercalado = 1
            elif ultimo_pad_tocado != celda:
                # Es el bombo contrario (alternado) = ¡Correcto!
                ultimo_pad_tocado = celda
                conteo_intercalado += 1
            else:
                # Tocó el mismo bombo dos veces seguidas = ¡Error! Se reinicia el conteo
                conteo_intercalado = 1 

        # Dibujar UI (Solo pintamos los dos bombos para enfocar la atencion)
        color_figura = (0, 255, 0) if estado_red[celda] or estado_verde[celda] else (255, 255, 255)
        cv2.ellipse(frame, (cx, cy), (ancho // 2, alto // 2), 0, 0, 360, color_figura, 3)

    # Instrucciones en pantalla
    cv2.putText(frame, "Tome asiento: Toca los dos bombos alternando", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 100, 255), 2)
    cv2.putText(frame, f"Aciertos consecutivos: {conteo_intercalado}/5", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)

    torso_x1, torso_y1 = 250, 80
    torso_x2, torso_y2 = 390, 240
    # Dibujar el rectángulo (Color Amarillo Cyan)
    cv2.rectangle(frame, (torso_x1, torso_y1), (torso_x2, torso_y2), (255, 255, 0), 2)
    # Etiqueta de texto para el recuadro
    espaciado_y = 50
    for linea in ["Ubica tu", "torso", "aqui"]:
        cv2.putText(frame, linea, (torso_x1 + 25, torso_y1 + espaciado_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        espaciado_y += 25 # Añade 25 píxeles hacia abajo para la siguiente línea
    cv2.imshow('Deteccion de Bateria', frame)

    # Si llegó a 5 alternados, terminamos el bucle de calibración
    if conteo_intercalado >= 5:
        # Texto de éxito antes de pasar a la siguiente fase
        cv2.putText(frame, "COMPLETADO!", (200, 240), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3)
        cv2.imshow('Deteccion de Bateria', frame)
        cv2.waitKey(1500) # Congela la pantalla 1.5 seg para que el usuario vea que pasó
        break

    if cv2.waitKey(1) & 0xFF == ord('q'):
        break

# Bucle de posicionamiento de la persona
import time # <- Asegúrate de que esta línea esté también arriba de tu archivo general.

pads_init = {
    # Base
    (0, 0): (200, 440, 110, 30),  # Bombo Izq
    (0, 1): (400, 450, 110, 30),  # Bombo Der

    # Capa 1 
    (1, 0): (200, 330, 110, 30),  
    (1, 1): (440, 330, 110, 30),  

    # Capa 2
    (2, 0): (100, 240, 110, 30),  # Medio Izq
    (2, 1): (540, 240, 110, 30),  # Medio Der

    # Capa 3
    (3, 0): (200, 150, 110, 30),  
    (3, 1): (440, 150, 110, 30),  

    # Capa 4
    (4, 0): (280, 60, 110, 30),  # Alto Izq
    (4, 1): (360, 60, 110, 30),  # Alto Der
}

ovales_init = {}
areas_totales_init = {}
mascaras_roi_init = {}

for celda, (cx, cy, ancho, alto) in pads_init.items():
    x1 = cx - (ancho // 2)
    y1 = cy - (alto // 2)
    x2 = cx + (ancho // 2)
    y2 = cy + (alto // 2)
    ovales_init[celda] = (cx, cy, ancho, alto, x1, y1, x2, y2)
    
    mask = np.zeros((alto, ancho), dtype=np.uint8)
    cv2.ellipse(mask, (ancho // 2, alto // 2), (ancho // 2, alto // 2), 0, 0, 360, 255, -1)
    mascaras_roi_init[celda] = mask
    areas_totales_init[celda] = cv2.countNonZero(mask)

estado_red_init = {celda: False for celda in ovales_init}
estado_verde_init = {celda: False for celda in ovales_init}

# Definir la secuencia de ida y vuelta
orden_ida = [
    (0, 0), (0, 1), 
    (1, 0), (1, 1), 
    (2, 0), (2, 1), 
    (3, 0), (3, 1), 
    (4, 0), (4, 1)
]
# La vuelta invierte la lista omitiendo el último para no repetir (4,1)
secuencia_objetivo = orden_ida + orden_ida[-2::-1]
indice_actual = 0
fase_espera = False
tiempo_inicio_espera = 0

while True:
    ret, frame = cap.read()
    if not ret: break
    frame = cv2.flip(frame, 1)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    mask_red = cv2.inRange(hsv, lower_red, upper_red)
    mask_green = cv2.inRange(hsv, lower_green, upper_green)
    
    kernel = np.ones((5,5), np.uint8)
    mask_red = cv2.morphologyEx(mask_red, cv2.MORPH_OPEN, kernel)
    mask_green = cv2.morphologyEx(mask_green, cv2.MORPH_OPEN, kernel)

    algun_pad_tocado_ahora = False

    for celda, (cx, cy, ancho, alto, x1, y1, x2, y2) in ovales_init.items():
        x1_safe, y1_safe = max(0, x1), max(0, y1)
        x2_safe, y2_safe = min(640, x2), min(480, y2)
        
        roi_red_rect = mask_red[y1_safe:y2_safe, x1_safe:x2_safe]
        roi_green_rect = mask_green[y1_safe:y2_safe, x1_safe:x2_safe]
        
        mx1, my1 = x1_safe - x1, y1_safe - y1
        mx2, my2 = mx1 + (x2_safe - x1_safe), my1 + (y2_safe - y1_safe)
        mask_oval_safe = mascaras_roi_init[celda][my1:my2, mx1:mx2]
        
        roi_red = cv2.bitwise_and(roi_red_rect, mask_oval_safe)
        roi_green = cv2.bitwise_and(roi_green_rect, mask_oval_safe)
        
        area_total = areas_totales_init[celda]
        if area_total == 0: continue

        porcentaje_red = cv2.countNonZero(roi_red) / area_total
        porcentaje_green = cv2.countNonZero(roi_green) / area_total

        tocado_este_frame = False

        if porcentaje_red > 0.005:
            if not estado_red_init[celda]:
                estado_red_init[celda] = True
                tocado_este_frame = True
        else:
            estado_red_init[celda] = False

        if porcentaje_green > 0.005:
            if not estado_verde_init[celda]:
                estado_verde_init[celda] = True
                tocado_este_frame = True
        else:
            estado_verde_init[celda] = False

        if tocado_este_frame:
            if (1, 1) in sonidos and sonidos[(1, 1)]:
                sonidos[(1, 1)].play()

            algun_pad_tocado_ahora = True
            if fase_espera:
                # Si estamos en la espera de 5 segundos y toca cualquier cosa, se reinicia.
                fase_espera = False
                indice_actual = 0
            elif indice_actual < len(secuencia_objetivo) and celda == secuencia_objetivo[indice_actual]:
                # Si tocó el correcto, avanza en la secuencia
                indice_actual += 1
                if indice_actual >= len(secuencia_objetivo):
                    fase_espera = True
                    tiempo_inicio_espera = time.time()

        # Feedback visual
        if fase_espera:
            color_figura = (0, 255, 0) # Todo verde si lo logró
            grosor = 2
        elif indice_actual < len(secuencia_objetivo) and celda == secuencia_objetivo[indice_actual]:
            color_figura = (0, 255, 255) # Amarillo remarcado para el pad que DEBE tocar ahora
            grosor = 4
        else:
            color_figura = (255, 255, 255) # Blanco para los inactivos
            grosor = 1

        cv2.ellipse(frame, (cx, cy), (ancho // 2, alto // 2), 0, 0, 360, color_figura, grosor)

    # Textos de interfaz e instrucciones
    if not fase_espera:
        cv2.putText(frame, f"Prueba: Toca el pad amarillo", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,255), 2)
        progreso = int((indice_actual / len(secuencia_objetivo)) * 100)
        cv2.putText(frame, f"Progreso: {progreso}%", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2)
    else:
        tiempo_restante = 5 - int(time.time() - tiempo_inicio_espera)
        cv2.putText(frame, "¡Excelente, mantente asi!", (80, 200), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 3)
        cv2.putText(frame, f"Comenzando en {tiempo_restante}...", (160, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)
        cv2.putText(frame, "Toca cualquier pad para volver a posicionarte", (30, 300), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,165,255), 2)
        
        if time.time() - tiempo_inicio_espera >= 5:
            # Pasa el tiempo de espera, se rompe el bucle de calibración 
            break
    
    torso_x1, torso_y1 = 250, 80
    torso_x2, torso_y2 = 390, 240
    # Dibujar el rectángulo (Color Amarillo Cyan)
    cv2.rectangle(frame, (torso_x1, torso_y1), (torso_x2, torso_y2), (255, 255, 0), 2)
    # Etiqueta de texto para el recuadro
    espaciado_y = 50
    for linea in ["Ubica tu", "torso", "aqui"]:
        cv2.putText(frame, linea, (torso_x1 + 25, torso_y1 + espaciado_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        espaciado_y += 25 # Añade 25 píxeles hacia abajo para la siguiente línea
    
    cv2.imshow('Deteccion de Bateria', frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        # Permite salir del programa sin necesidad de completar
        break


# Bucle de ubicación de elementos
# Partimos de los dos bombos ya ubicados
pads_game = {
    (2, 1): (400, 450, 110, 30)   # Bombo 
}

# Lista en orden de los elementos que vamos a posicionar
# Cada uno contiene: Nombre, Llave de celda, Ancho, Alto
elementos_a_ubicar = [
    ("Caja (Tarola)", (1, 1), 140, 50),
    ("Hi-Hat", (1, 0), 110, 30),
    ("Tom Superior", (0, 1), 100, 50),
    ("Tom Grave", (2, 2), 110, 30),
    ("Platillo", (0, 0), 110, 60)
]

indice_elemento = 0
fijando = False
tiempo_inicio_fijacion = 0

while indice_elemento < len(elementos_a_ubicar):
    ret, frame = cap.read()
    if not ret: break
    
    frame = cv2.flip(frame, 1)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    # La baqueta que usamos como puntero (En tu código está como rojo/azul y verde)
    mask_green = cv2.inRange(hsv, lower_red, upper_red)
    mask_red = cv2.inRange(hsv, lower_green, upper_green)
    
    kernel = np.ones((5,5), np.uint8)
    mask_red = cv2.morphologyEx(mask_red, cv2.MORPH_OPEN, kernel)
    mask_green = cv2.morphologyEx(mask_green, cv2.MORPH_OPEN, kernel)

    # Dibuja de fondo todos los elementos que ya hemos guardado en pads_game
    for c, (cx, cy, w, h) in pads_game.items():
        cv2.ellipse(frame, (cx, cy), (w // 2, h // 2), 0, 0, 360, (255, 255, 255), 2)

    nombre_elem, celda, ancho, alto = elementos_a_ubicar[indice_elemento]

    # Encontramos la baqueta principal buscando "contornos" en la cámara
    contornos_red, _ = cv2.findContours(mask_red, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    
    if contornos_red:
        # Tomar solo el área más grande para ignorar ruidos del fondo
        c_red = max(contornos_red, key=cv2.contourArea)
        if cv2.contourArea(c_red) > 150:
            M = cv2.moments(c_red)
            if M["m00"] != 0:
                # Calculamos el Centro (X, Y) exacto de la baqueta
                cx = int(M["m10"] / M["m00"])
                cy = int(M["m01"] / M["m00"])

                # Dibujamos un rectángulo del tamaño del elemento usando el centro de la baqueta
                x1 = max(0, cx - ancho // 2)
                y1 = max(0, cy - alto // 2)
                x2 = min(640, cx + ancho // 2)
                y2 = min(480, cy + alto // 2)

                # Extraemos la región pero sobre la OTRA baqueta para detectar si entró en la zona
                roi_green = mask_green[y1:y2, x1:x2]
                
                # 1. Crear una capa superpuesta para pintar el relleno con transparencia
                overlay = frame.copy()
                
                if cv2.countNonZero(roi_green) > 60: # Ambas baquetas están cruzadas dentro del cuadro
                    if not fijando:
                        if (1, 1) in sonidos and sonidos[(1, 1)]:
                            sonidos[(1, 1)].play()
                        fijando = True
                        tiempo_inicio_fijacion = time.time()
                    
                    tiempo_transcurrido = time.time() - tiempo_inicio_fijacion
                    tiempo_restante = 5 - int(tiempo_transcurrido)
                    
                    # Cuadrado AMARILLO: Relleno transparente + Borde sólido
                    cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 255, 255), cv2.FILLED)
                    cv2.addWeighted(overlay, 0.4, frame, 0.6, 0, frame) # 40% de opacidad para el relleno
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 255), 3) # Borde amarillo
                    
                    cv2.putText(frame, f"FIJANDO... {tiempo_restante}s", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

                    if tiempo_transcurrido >= 5:
                        # Si llega a cero, guardamos en memoria y pasamos al siguiente
                        pads_game[celda] = (cx, cy, ancho, alto)
                        indice_elemento += 1
                        fijando = False
                        
                        # Efecto Cuadro VERDE de completado (transparente)
                        overlay = frame.copy()
                        cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 255, 0), cv2.FILLED)
                        cv2.addWeighted(overlay, 0.6, frame, 0.4, 0, frame) # 60% opacidad al confirmar
                        cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 3) # Borde verde
                        
                        cv2.imshow('Deteccion de Bateria', frame)
                        cv2.waitKey(600) # Mostrar cuadro VERDE medio segundo
                else:
                    # Si no ha acercado la otra baqueta, resetea el timer
                    fijando = False
                    
                    # Cuadro AZUL original: Relleno transparente + Borde sólido
                    cv2.rectangle(overlay, (x1, y1), (x2, y2), (255, 0, 0), cv2.FILLED)
                    cv2.addWeighted(overlay, 0.3, frame, 0.7, 0, frame) # 30% opacidad para esperar
                    cv2.rectangle(frame, (x1, y1), (x2, y2), (255, 0, 0), 2) # Borde azul
                    
                    cv2.putText(frame, f"Acerca la baqueta verde aqui!", (30, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)            
            else:
                fijando = False
        else:
            fijando = False
    else:
        fijando = False

    cv2.putText(frame, f"Ubicar Elementos: {indice_elemento + 1}/5", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 165, 255), 2)
    cv2.putText(frame, f"Actual: {nombre_elem}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
    cv2.imshow('Deteccion de Bateria', frame)

    if cv2.waitKey(1) & 0xFF == ord('q'):
        pads_game = {
            # Platillos y Charles (Arco superior)
            (0, 0): (170, 70, 110, 60),  # Platillo Crash 1 (círculo grande)
            (0, 1): (400, 70, 90, 50),  # Tom Alto 1
            
            # Toms y Caja (Arco central-inferior)
            (1, 0): (190, 200, 110, 30),  # Hi-Hat
            (1, 1): (320, 240, 140, 50),  # Caja (Snare)
            (1, 2): (480, 240, 140, 50),  # Tom inferior
            
            # Base
            (2, 1): (400, 450, 110, 30),  # Bombo 
        }

        break

lista_elementos = []

# Recorremos lo que el usuario guardó (o los valores por defecto si presionó 'q')
for celda, datos in pads_game.items():
    cx, cy, ancho, alto = datos
    
    # Creamos el diccionario individual para cada pad
    pad_data = {
        "elemento": str(celda), # Usaremos la tupla como texto, ej: "(1, 1)"
        "x": cx,
        "y": cy
    }
    lista_elementos.append(pad_data)

# Armamos el mensaje final completo
mensaje_calibracion = {
    "tipo": "configuracion", # Etiqueta útil para que tu Pygame sepa cómo procesarlo
    "dim_x": 640,
    "dim_y": 480,
    "elementos": lista_elementos
}

# Enviamos el paquete por UDP
sock.sendto(json.dumps(mensaje_calibracion).encode('utf-8'), (UDP_IP, UDP_PORT))

# == Preparar variables para el Bucle Jugable Final ==
# Esto sobreescribe los 'ovales' estáticos iniciales en favor de los que el usuario construyó.
ovales = {}
areas_totales = {}
mascaras_roi = {}

for celda, (cx, cy, ancho, alto) in pads_game.items():
    x1 = cx - (ancho // 2)
    y1 = cy - (alto // 2)
    x2 = cx + (ancho // 2)
    y2 = cy + (alto // 2)
    ovales[celda] = (cx, cy, ancho, alto, x1, y1, x2, y2)
    
    mask = np.zeros((alto, ancho), dtype=np.uint8)
    cv2.ellipse(mask, (ancho // 2, alto // 2), (ancho // 2, alto // 2), 0, 0, 360, 255, -1)
    mascaras_roi[celda] = mask
    areas_totales[celda] = cv2.countNonZero(mask)

estado_red = {celda: False for celda in ovales}
estado_verde = {celda: False for celda in ovales}

mapa_teclas = {
    ord('1'): (0, 0), # Platillo Crash 1
    ord('2'): (0, 1), # Tom Alto 1
    ord('3'): (1, 0), # Hi-Hat
    ord('4'): (1, 1), # Caja (Snare)
    ord('5'): (2, 1), # Bombo
    ord('6'): (2, 2) # Tom de Piso (Floor Tom)
}
#Bucle jugable
while True:
    ret, frame = cap.read()
    if not ret:
        print("No se pudo acceder a la cámara.")
        break

    frame = cv2.flip(frame, 1)
    hsv = cv2.cvtColor(frame, cv2.COLOR_BGR2HSV)

    # 1. Crear máscaras (Asumimos que configuraste lower_blue y upper_blue para tu pie)
    mask_red = cv2.inRange(hsv, lower_red, upper_red)
    mask_green = cv2.inRange(hsv, lower_green, upper_green)
    mask_pie = cv2.inRange(hsv, lower_red, upper_red) # NUEVO COLOR PARA EL PIE
    
    # Filtro morfológico
    kernel = np.ones((5,5), np.uint8)
    mask_red = cv2.morphologyEx(mask_red, cv2.MORPH_OPEN, kernel)
    mask_green = cv2.morphologyEx(mask_green, cv2.MORPH_OPEN, kernel)
    mask_pie = cv2.morphologyEx(mask_pie, cv2.MORPH_OPEN, kernel)

    # =================================================================
    # NUEVO: DIVISIÓN DE PANTALLA EN Y = 360
    # =================================================================
    # mask[y_inicio : y_fin, x_inicio : x_fin] = 0 (0 es color negro)

    mask_left = mask_red.copy()
    mask_right = mask_green.copy()
    # Para las manos (Rojo y Verde): Apagamos todo desde y=360 hasta abajo
    mask_left[360:, :] = 0
    mask_right[360:, :] = 0

    # Para el pie (Tercer color): Apagamos todo desde arriba hasta y=360
    mask_pie[:360, :] = 0

    # Opcional: Dibujar una línea en la pantalla para que tú veas el límite físico
    cv2.line(frame, (0, 360), (640, 360), (255, 255, 255), 2)
    cv2.putText(frame, "ZONA DE MANOS", (10, 350), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 1)
    cv2.putText(frame, "ZONA DE PIE", (10, 380), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255,255,255), 1)


    # =================================================================

    # =================================================================
    # ENVÍO CONSTANTE DE POSICIONES
    # =================================================================
    centro_rosa = obtener_centro_global(mask_left)
    centro_verde = obtener_centro_global(mask_right)
    centro_pie = obtener_centro_global(mask_pie) # Calculamos el tercer centro

    mensaje_posiciones = {
        "tipo": "posicion",
        "stick_1_x": centro_rosa[0] if centro_rosa else None,
        "stick_1_y": centro_rosa[1] if centro_rosa else None,
        "stick_2_x": centro_verde[0] if centro_verde else None,
        "stick_2_y": centro_verde[1] if centro_verde else None,
        "stick_3_x": centro_pie[0] if centro_pie else None, # Nuevo campo para el pie
        "stick_3_y": centro_pie[1] if centro_pie else None
    }

    # Enviar a Pygame
    sock.sendto(json.dumps(mensaje_posiciones).encode('utf-8'), (UDP_IP, UDP_PORT))
    # =================================================================

    # =================================================================
    # NUEVO: DIBUJAR UNA "X" EN LOS CENTROS DETECTADOS
    # =================================================================
    # cv2.drawMarker(imagen, (x, y), color_bgr, tipo_marcador, tamaño, grosor)
    
    if centro_rosa:
        # Dibuja una 'X' color Magenta
        cv2.drawMarker(frame, (centro_rosa[0], centro_rosa[1]), (255, 0, 255), cv2.MARKER_TILTED_CROSS, 20, 3)
        cv2.putText(frame, "Mano R", (centro_rosa[0] + 10, centro_rosa[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 255), 2)
        
    if centro_verde:
        # Dibuja una 'X' color Verde
        cv2.drawMarker(frame, (centro_verde[0], centro_verde[1]), (0, 255, 0), cv2.MARKER_TILTED_CROSS, 20, 3)
        cv2.putText(frame, "Mano V", (centro_verde[0] + 10, centro_verde[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)

    if centro_pie:
        # Dibuja una 'X' color Azul
        cv2.drawMarker(frame, (centro_pie[0], centro_pie[1]), (255, 0, 0), cv2.MARKER_TILTED_CROSS, 20, 3)
        cv2.putText(frame, "Pie", (centro_pie[0] + 10, centro_pie[1] - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 2)
    # =================================================================

    # Recorrer cada cuadrado
    for celda, (cx, cy, ancho, alto, x1, y1, x2, y2) in ovales.items():
        # 1. Ajustar límites para evitar índices negativos o fuera de pantalla
        x1_safe = max(0, x1)
        y1_safe = max(0, y1)
        x2_safe = min(640, x2)
        y2_safe = min(480, y2)
        
        # Extraer las regiones del video usando los límites seguros
        roi_red_rect = mask_red[y1_safe:y2_safe, x1_safe:x2_safe]
        roi_green_rect = mask_green[y1_safe:y2_safe, x1_safe:x2_safe]
        
        # 2. Extraer la sección correspondiente de nuestra mascará elíptica pre-generada
        mx1 = x1_safe - x1
        my1 = y1_safe - y1
        mx2 = mx1 + (x2_safe - x1_safe)
        my2 = my1 + (y2_safe - y1_safe)
        
        mask_oval_safe = mascaras_roi[celda][my1:my2, mx1:mx2]
        
        # Superponer la máscara en forma de óvalo para ignorar las esquinas
        roi_red = cv2.bitwise_and(roi_red_rect, mask_oval_safe)
        roi_green = cv2.bitwise_and(roi_green_rect, mask_oval_safe)
        
        # Usaremos el área de la máscara recortada
        area_total = cv2.countNonZero(mask_oval_safe)
        if area_total == 0: 
            continue 

        porcentaje_red = cv2.countNonZero(roi_red) / area_total
        porcentaje_green = cv2.countNonZero(roi_green) / area_total

        # --- Lógica del Switch para el ROSA ---
        if porcentaje_red > 0.005:
            if not estado_red[celda]:
                estado_red[celda] = True
                if sonidos[celda]: 
                    sonidos[celda].play()
                
                # NUEVO: ENVIAR EVENTO DE GOLPE (ROSA)
                mensaje_golpe = {
                    "tipo": "golpe",
                    "pad": str(celda) # Convertimos la tupla (0,1) a string "(0, 1)" para JSON
                }
                sock.sendto(json.dumps(mensaje_golpe).encode('utf-8'), (UDP_IP, UDP_PORT))

        elif porcentaje_red < 0.005:
            estado_red[celda] = False

        # --- Lógica del Switch para el VERDE ---
        if porcentaje_green > 0.005:
            if not estado_verde[celda]:
                estado_verde[celda] = True
                if sonidos[celda]: 
                    sonidos[celda].play()
                
                # NUEVO: ENVIAR EVENTO DE GOLPE (VERDE)
                mensaje_golpe = {
                    "tipo": "golpe",
                    "pad": str(celda)
                }
                sock.sendto(json.dumps(mensaje_golpe).encode('utf-8'), (UDP_IP, UDP_PORT))

        elif porcentaje_green < 0.005:
            estado_verde[celda] = False

        

        # --- Determinar color de interfaz ---
        if estado_red[celda] and estado_verde[celda]:
            color_figura = (255, 255, 0) # Cyan
            grosor = 4
        elif estado_red[celda]:
            color_figura = (255, 0, 0) # Azul (BGR)
            grosor = 4
        elif estado_verde[celda]:
            color_figura = (0, 255, 0) # Verde
            grosor = 4
        else:
            color_figura = (255, 255, 255) # Blanco
            grosor = 2
            
            if (estado_red[celda] and porcentaje_red >= 0.005) or \
               (estado_verde[celda] and porcentaje_green >= 0.005):
                 color_figura = (0, 255, 255) # Amarillo advertencia

        # Dibujar óvalo (Elipse) en lugar del rectángulo
        cv2.ellipse(frame, (cx, cy), (ancho // 2, alto // 2), 0, 0, 360, color_figura, grosor)

    # Combinamos ambas máscaras solo para visualizarlas en la ventana de depuración
    mask_combinada = cv2.bitwise_or(mask_red, mask_green)

    # Textos e instrucciones
    cv2.putText(frame, "Bateria Virtual", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,0,0), 2)

    cv2.imshow('Deteccion de Bateria', frame)
    cv2.imshow('Mascara Combinada (IA)', mask_combinada)

    tecla = cv2.waitKey(1) & 0xFF

    if tecla == ord('q'):
        break  # Salir del programa
        
    # Si la tecla presionada está en nuestro mapa (es decir, es del 1 al 6)
    elif tecla in mapa_teclas:
        celda_manual = mapa_teclas[tecla]
        
        # 1. Reproducir el sonido localmente
        if celda_manual in sonidos and sonidos[celda_manual]:
            sonidos[celda_manual].play()
            
        # 2. Enviar el evento por UDP al servicio de Pygame
        mensaje_golpe = {
            "tipo": "golpe",
            "pad": str(celda_manual),
            "color": "teclado" # Un identificador útil para saber que fue manual
        }
        sock.sendto(json.dumps(mensaje_golpe).encode('utf-8'), (UDP_IP, UDP_PORT))
        
        # Un pequeño aviso en consola para que sepas que funcionó
        print(f"Prueba manual: Pad {celda_manual} activado con el teclado.")

cap.release()
cv2.destroyAllWindows()
pygame.quit()