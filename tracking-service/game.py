# juego.py
import cv2
import numpy as np
import pygame
import os
import socket
import json
import time

UDP_IP = "127.0.0.1"
UDP_PORT = 5051
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

# Función auxiliar para encontrar el centro (X, Y) del color principal en pantalla
def obtener_centro_global(mask):
    contornos, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if contornos:
        # Quedarnos con la mancha de color más grande
        c_max = max(contornos, key=cv2.contourArea)
        if cv2.contourArea(c_max) > 200: # Ignorar manchas muy pequeñas
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
# ... [Copia aquí los rangos de colores HSV] ...
# Rojo / rosado limpio
# Color 1 (Amarillo/Naranja) - Reemplaza al antiguo "Rojo"
lower_red1 = np.array([105, 180, 80])
upper_red1 = np.array([125, 255, 255])

# El naranja no necesita dos rangos (a diferencia del rojo puro), 
# pero repetimos los valores para no romper tu variable mask_red = red1 + red2
lower_red2 = lower_red1 
upper_red2 = upper_red1

# Color 2  - Reemplaza al antiguo "Verde"
lower_green = np.array([70, 200, 40])
upper_green = np.array([85, 255, 180])

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



# CARGAR LA CONFIGURACIÓN PREVIAMENTE GUARDADA
if not os.path.exists("config_pads.json"):
    print("Error: No se encontró 'config_pads.json'. ¡Ejecuta calibracion.py primero!")
    exit(1)

with open("config_pads.json", "r") as f:
    config = json.load(f)
time.sleep(5)
sock.sendto(json.dumps(config).encode('utf-8'), (UDP_IP, UDP_PORT))

# Reconstruir los ovales en base al archivo JSON
ovales = {}
areas_totales = {}
mascaras_roi = {}

for item in config["elementos"]:
    # Reconvertir el string a tupla. Ej: "(0, 1)" -> (0, 1)
    celda = eval(item["elemento"]) 
    cx = item["x"]
    cy = item["y"]
    ancho = item["w"]
    alto = item["h"]

    x1, y1 = cx - (ancho // 2), cy - (alto // 2)
    x2, y2 = cx + (ancho // 2), cy + (alto // 2)
    ovales[celda] = (cx, cy, ancho, alto, x1, y1, x2, y2)
    
    mask = np.zeros((alto, ancho), dtype=np.uint8)
    cv2.ellipse(mask, (ancho // 2, alto // 2), (ancho // 2, alto // 2), 0, 0, 360, 255, -1)
    mascaras_roi[celda] = mask
    areas_totales[celda] = cv2.countNonZero(mask)

estado_red = {celda: False for celda in ovales}
estado_verde = {celda: False for celda in ovales}

# Inicializar cámara
cap = cv2.VideoCapture(0)
cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)


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
    red1 = cv2.inRange(hsv, lower_red1, upper_red1)
    red2 = cv2.inRange(hsv, lower_red2, upper_red2)
    mask_red = red1 + red2
    mask_green = cv2.inRange(hsv, lower_green, upper_green)
    mask_pie = cv2.inRange(hsv, lower_green, upper_green) # NUEVO COLOR PARA EL PIE
    
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