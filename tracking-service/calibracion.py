# calibracion.py
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

# --- Constantes y Configuración de Colores HSV ---
FRAME_WIDTH = 640
FRAME_HEIGHT = 480

LOWER_RED1 = np.array([0, 85, 150])
UPPER_RED1 = np.array([10, 255, 255])
LOWER_RED2 = np.array([170, 85, 150])
UPPER_RED2 = np.array([179, 255, 255])

LOWER_GREEN = np.array([75, 50, 120])
UPPER_GREEN = np.array([100, 255, 255])


def inicializar_audio():
    """Inicializa pygame, ajusta los canales y carga los sonidos en un diccionario."""
    pygame.mixer.pre_init(44100, -16, 2, 512)
    pygame.mixer.init()
    pygame.mixer.set_num_channels(32)

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
            print(f"Aviso: No se encontró el archivo de audio '{archivo}'.")
            sonidos[celda] = None
    return sonidos


def inicializar_camara():
    """Inicializa y configura la resolución de la cámara web."""
    cap = cv2.VideoCapture(0)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
    return cap

def oscurecer_fondo(frame, opacidad=0.6):
    """Opaca el fondo aplicando una capa negra semitransparente."""
    overlay = frame.copy()
    cv2.rectangle(overlay, (0, 0), (FRAME_WIDTH, FRAME_HEIGHT), (0, 0, 0), -1)
    cv2.addWeighted(overlay, opacidad, frame, 1 - opacidad, 0, frame)

def procesar_frame(frame, opacidad_fondo=0.0):
    """Convierte el frame, aplica máscaras HSV y morfología. Devuelve el frame volteado y las máscaras."""
    frame_flipped = cv2.flip(frame, 1)
    hsv = cv2.cvtColor(frame_flipped, cv2.COLOR_BGR2HSV)

    # Máscara para Rojo (Se divide en 2 por el inicio/fin del rango polar)
    red1 = cv2.inRange(hsv, LOWER_RED1, UPPER_RED1)
    red2 = cv2.inRange(hsv, LOWER_RED2, UPPER_RED2)
    mask_red = red1 + red2
    
    # Máscara para Verde
    mask_green = cv2.inRange(hsv, LOWER_GREEN, UPPER_GREEN)

    # Filtrar ruido
    kernel = np.ones((5, 5), np.uint8)
    mask_red = cv2.morphologyEx(mask_red, cv2.MORPH_OPEN, kernel)
    mask_green = cv2.morphologyEx(mask_green, cv2.MORPH_OPEN, kernel)

    # NUEVO: Si se pide opacidad, oscurecemos el fondo ANTES de pintar las baquetas
    if opacidad_fondo > 0:
        oscurecer_fondo(frame_flipped, opacidad=opacidad_fondo)

    # Feedback visual (Pintar píxeles detectados brillantes sobre el fondo oscuro)
    frame_flipped[mask_red > 0] = [0, 0, 255]
    frame_flipped[mask_green > 0] = [0, 255, 0]

    return frame_flipped, mask_red, mask_green


def crear_diccionarios_ovales(pads_dict):
    """Devuelve los ovales generados, mascaras, áreas y los estados por defecto."""
    ovales = {}
    mascaras_roi = {}
    areas = {}
    for celda, (cx, cy, ancho, alto) in pads_dict.items():
        x1, y1 = cx - (ancho // 2), cy - (alto // 2)
        x2, y2 = cx + (ancho // 2), cy + (alto // 2)
        
        ovales[celda] = (cx, cy, ancho, alto, x1, y1, x2, y2)
        mask = np.zeros((alto, ancho), dtype=np.uint8)
        cv2.ellipse(mask, (ancho // 2, alto // 2), (ancho // 2, alto // 2), 0, 0, 360, 255, -1)
        mascaras_roi[celda] = mask
        areas[celda] = cv2.countNonZero(mask)

    st_red = {celda: False for celda in pads_dict}
    st_green = {celda: False for celda in pads_dict}

    return ovales, mascaras_roi, areas, st_red, st_green


def evaluar_impactos(ovales, mascaras_roi, areas, mask_red, mask_green, estado_red, estado_verde, umbral=0.005):
    """Evalúa si las baquetas han impactado alguna almohadilla. Retorna los toques del frame actual."""
    tocado_celdas = []
    
    for celda, (cx, cy, ancho, alto, x1, y1, x2, y2) in ovales.items():
        x1_s, y1_s = max(0, x1), max(0, y1)
        x2_s, y2_s = min(FRAME_WIDTH, x2), min(FRAME_HEIGHT, y2)
        
        roi_r_rect = mask_red[y1_s:y2_s, x1_s:x2_s]
        roi_g_rect = mask_green[y1_s:y2_s, x1_s:x2_s]
        
        mx1, my1 = x1_s - x1, y1_s - y1
        mx2, my2 = mx1 + (x2_s - x1_s), my1 + (y2_s - y1_s)
        mask_oval_s = mascaras_roi[celda][my1:my2, mx1:mx2]
        
        area_total = areas[celda]
        if area_total == 0: continue

        # Aislar impacto
        roi_r = cv2.bitwise_and(roi_r_rect, mask_oval_s)
        roi_g = cv2.bitwise_and(roi_g_rect, mask_oval_s)

        porcentaje_r = cv2.countNonZero(roi_r) / area_total
        porcentaje_g = cv2.countNonZero(roi_g) / area_total

        tocado_este_frame = False

        if porcentaje_r > umbral:
            if not estado_red[celda]:
                estado_red[celda] = True
                tocado_este_frame = True
        else:
            estado_red[celda] = False

        if porcentaje_g > umbral:
            if not estado_verde[celda]:
                estado_verde[celda] = True
                tocado_este_frame = True
        else:
            estado_verde[celda] = False

        if tocado_este_frame:
            tocado_celdas.append(celda)

    return tocado_celdas


def dibujar_guia_torso(frame):
    """Dibuja el recuadro guía para el torso en pantalla."""
    tx1, ty1 = 250, 80
    tx2, ty2 = 390, 240
    cv2.rectangle(frame, (tx1, ty1), (tx2, ty2), (255, 255, 0), 2)
    espaciado_y = 50
    for linea in ["Ubica tu", "torso", "aqui"]:
        cv2.putText(frame, linea, (tx1 + 25, ty1 + espaciado_y), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)
        espaciado_y += 25

def superponer_imagen_con_alfa(fondo, imagen_rgba, x_cent, y_cent):
    """Superpone una imagen con canal Alpha (RGBA) sobre el fondo (BGR) centrado en x_cent, y_cent."""
    alto, ancho = imagen_rgba.shape[:2]
    
    # Coordenadas ideales superior izquierda
    x = x_cent - ancho // 2
    y = y_cent - alto // 2

    # Límites de dibujo permitidos en la pantalla (fondo)
    y1 = max(0, y)
    y2 = min(fondo.shape[0], y + alto)
    x1 = max(0, x)
    x2 = min(fondo.shape[1], x + ancho)

    # Límites de lectura permitidos en la imagen de origen (evita dimensiones impares rotas)
    y1o = max(0, -y)
    y2o = min(alto, fondo.shape[0] - y)
    x1o = max(0, -x)
    x2o = min(ancho, fondo.shape[1] - x)

    # Si la imagen está completamente fuera de la pantalla, no hacer nada
    if y1 >= y2 or x1 >= x2 or y1o >= y2o or x1o >= x2o:
        return fondo

    alpha_s = imagen_rgba[y1o:y2o, x1o:x2o, 3] / 255.0
    alpha_l = 1.0 - alpha_s

    # Combinar canales de color BGR
    for c in range(0, 3):
        fondo[y1:y2, x1:x2, c] = (alpha_s * imagen_rgba[y1o:y2o, x1o:x2o, c] +
                                  alpha_l * fondo[y1:y2, x1:x2, c])
    return fondo

def fase1_calibracion_bombos(cap, sonidos):
    """Obliga al usuario a tocar alternadamente los dos bombos 5 veces."""
    pads_config = {(2, 0): (200, 440, 110, 30), (2, 1): (400, 450, 110, 30)}
    ovales, mast_roi, areas, est_r, est_g = crear_diccionarios_ovales(pads_config)
    
    ultimo_pad = None
    conteo = 0

    while True:
        ret, frame_raw = cap.read()
        if not ret: break
        frame, mask_r, mask_g = procesar_frame(frame_raw)

        # Evaluar impactos solo para los pads activos
        tocados = evaluar_impactos(ovales, mast_roi, areas, mask_r, mask_g, est_r, est_g)

        for celda in tocados:
            if sonidos[celda]: sonidos[celda].play()
            
            if ultimo_pad is None:
                ultimo_pad = celda
                conteo = 1
            elif ultimo_pad != celda:
                ultimo_pad = celda
                conteo += 1
            else:
                conteo = 1  # Falló la alternancia

        # Dibujar UI
        for celda, (cx, cy, cw, ch, *_) in ovales.items():
            color = (0, 255, 0) if est_r[celda] or est_g[celda] else (255, 255, 255)
            cv2.ellipse(frame, (cx, cy), (cw // 2, ch // 2), 0, 0, 360, color, 3)

        cv2.putText(frame, "Tome asiento: Toca los dos bombos alternando", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 100, 255), 2)
        cv2.putText(frame, f"Aciertos consecutivos: {conteo}/5", (10, 70), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 255, 0), 2)
        dibujar_guia_torso(frame)

        if conteo >= 5:
            cv2.putText(frame, "COMPLETADO!", (200, 240), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 0), 3)
            cv2.imshow('Deteccion de Bateria', frame)
            cv2.waitKey(1500)
            break

        cv2.imshow('Deteccion de Bateria', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break


def fase2_calibracion_posicionamiento(cap, sonidos):
    """Obliga al usuario a seguir una secuencia de notas para aprender los límites."""
    pads_init = {
        (0, 0): (200, 440, 110, 30), (0, 1): (400, 450, 110, 30),
        (1, 0): (200, 330, 110, 30), (1, 1): (440, 330, 110, 30),
        (2, 0): (100, 240, 110, 30), (2, 1): (540, 240, 110, 30),
        (3, 0): (200, 150, 110, 30), (3, 1): (440, 150, 110, 30),
        (4, 0): (280, 60, 110, 30),  (4, 1): (360, 60, 110, 30),
    }

    ovales, mast_roi, areas, est_r, est_g = crear_diccionarios_ovales(pads_init)
    
    orden = [(0,0),(0,1),(1,0),(1,1),(2,0),(2,1),(3,0),(3,1),(4,0),(4,1)]
    secuencia_objetivo = orden + orden[-2::-1]
    
    idx_actual = 0
    fase_espera = False
    inicio_espera = 0

    while True:
        ret, frame_raw = cap.read()
        if not ret: break
        frame, mask_r, mask_g = procesar_frame(frame_raw)

        tocados = evaluar_impactos(ovales, mast_roi, areas, mask_r, mask_g, est_r, est_g)
        
        if tocados:
            if (1, 1) in sonidos and sonidos[(1, 1)]: sonidos[(1, 1)].play()
            
            for celda in tocados:
                if fase_espera:
                    fase_espera = False
                    idx_actual = 0
                elif idx_actual < len(secuencia_objetivo) and celda == secuencia_objetivo[idx_actual]:
                    idx_actual += 1
                    if idx_actual >= len(secuencia_objetivo):
                        fase_espera = True
                        inicio_espera = time.time()


        # Dibujar Feedback UI
        for celda, (cx, cy, cw, ch, *_) in ovales.items():
            if fase_espera:
                cv2.ellipse(frame, (cx, cy), (cw // 2, ch // 2), 0, 0, 360, (0, 255, 0), 2)
            elif idx_actual < len(secuencia_objetivo) and celda == secuencia_objetivo[idx_actual]:
                cv2.ellipse(frame, (cx, cy), (cw // 2, ch // 2), 0, 0, 360, (0, 255, 255), 4)
            else:
                cv2.ellipse(frame, (cx, cy), (cw // 2, ch // 2), 0, 0, 360, (255, 255, 255), 1)

        # Textos de Interfaz
        if not fase_espera:
            cv2.putText(frame, "Prueba: Toca el pad amarillo", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0,255,255), 2)
            progreso = int((idx_actual / len(secuencia_objetivo)) * 100)
            cv2.putText(frame, f"Progreso: {progreso}%", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255,255,255), 2)
        else:
            t_restante = 5 - int(time.time() - inicio_espera)
            cv2.putText(frame, "¡Excelente, mantente asi!", (80, 200), cv2.FONT_HERSHEY_SIMPLEX, 1, (0,255,0), 3)
            cv2.putText(frame, f"Comenzando en {t_restante}...", (160, 250), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0,255,0), 2)
            cv2.putText(frame, "Toca cualquier pad para resetear", (30, 300), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0,165,255), 2)
            
            if time.time() - inicio_espera >= 5: break # Éxito final

        dibujar_guia_torso(frame)
        cv2.imshow('Deteccion de Bateria', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'): break

def fase3_eleccion_modo_de_juego(cap, sonidos):
    """Pide al usuario que elija el modo de juego usando las dos baquetas superpuestas."""
    
    # Cargar las imágenes PNG (Asegúrate de que las rutas sean correctas)
    img_tradicional = cv2.imread("assets/images/tradicional.png", cv2.IMREAD_UNCHANGED)
    if img_tradicional is not None and img_tradicional.shape[2] == 3:
        img_tradicional = cv2.cvtColor(img_tradicional, cv2.COLOR_BGR2BGRA)

    # Cargar Gamer (Es JPG, así que la forzamos a tener canal Alpha para la función)
    img_gamer = cv2.imread("assets/images/game.jpg", cv2.IMREAD_UNCHANGED)
    if img_gamer is not None and img_gamer.shape[2] == 3:
        img_gamer = cv2.cvtColor(img_gamer, cv2.COLOR_BGR2BGRA)

    # Ajusta el tamaño de las imágenes si son muy grandes (Ejemplo: 160x160 píxeles)
    if img_tradicional is not None:
        img_tradicional = cv2.resize(img_tradicional, (160, 160))
    if img_gamer is not None:
        img_gamer = cv2.resize(img_gamer, (160, 160))

    # 1. Definir las opciones: Nombre_Modo, Titulo, (x_centro, y_centro, ancho, alto), Imagen
    opciones = {
        "tradicional": ("Modo Tradicional", (160, 240, 200, 300), img_tradicional),
        "gamer": ("Modo Gamer", (480, 240, 200, 300), img_gamer)
    }
    
    fijando_modo = None
    inicio_fijacion = 0
    modo_seleccionado = None

    while modo_seleccionado is None:
        ret, frame_raw = cap.read()
        if not ret: break
        
        frame_flipped = cv2.flip(frame_raw, 1)
        hsv = cv2.cvtColor(frame_flipped, cv2.COLOR_BGR2HSV)
        r1 = cv2.inRange(hsv, LOWER_RED1, UPPER_RED1)
        r2 = cv2.inRange(hsv, LOWER_RED2, UPPER_RED2)
        mask_green = r1 + r2  
        mask_red = cv2.inRange(hsv, LOWER_GREEN, UPPER_GREEN)

        kernel = np.ones((5,5), np.uint8)
        mask_r = cv2.morphologyEx(mask_red, cv2.MORPH_OPEN, kernel)
        mask_g = cv2.morphologyEx(mask_green, cv2.MORPH_OPEN, kernel)

        

        contornos_red, _ = cv2.findContours(mask_r, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        baqueta_cx, baqueta_cy = -1, -1
        if contornos_red:
            c_red = max(contornos_red, key=cv2.contourArea)
            if cv2.contourArea(c_red) > 30:
                M = cv2.moments(c_red)
                if M["m00"] != 0:
                    baqueta_cx = int(M["m10"] / M["m00"])
                    baqueta_cy = int(M["m01"] / M["m00"])

        oscurecer_fondo(frame_flipped, opacidad=0.6)

        frame_flipped[mask_r > 0] = [0, 0, 255]
        frame_flipped[mask_g > 0] = [0, 255, 0]
        
        opcion_enfocada_actual = None
        
        for key_modo, (titulo, (cx, cy, ancho, alto), img) in opciones.items():
            x1, y1 = max(0, cx - ancho // 2), max(0, cy - alto // 2)
            x2, y2 = min(FRAME_WIDTH, cx + ancho // 2), min(FRAME_HEIGHT, cy + alto // 2)
            
            # Dibujar rectángulo de fondo oscuro
            overlay = frame_flipped.copy()
            cv2.rectangle(overlay, (x1, y1), (x2, y2), (20, 20, 20), cv2.FILLED)
            cv2.addWeighted(overlay, 0.4, frame_flipped, 0.6, 0, frame_flipped)
            
            # Dibujar la Imagen PNG si cargó correctamente
            if img is not None:
                frame_flipped = superponer_imagen_con_alfa(frame_flipped, img, cx, cy - 30)

            # Dibujar Textos
            cv2.rectangle(frame_flipped, (x1, y1), (x2, y2), (255, 255, 255), 2)
            cv2.putText(frame_flipped, titulo, (x1 + 10, y2 - 40), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

            # Lógica de fijación de puntero
            if x1 <= baqueta_cx <= x2 and y1 <= baqueta_cy <= y2:
                roi_green = mask_g[y1:y2, x1:x2]
                
                if cv2.countNonZero(roi_green) > 30:
                    opcion_enfocada_actual = key_modo
                    
                    if fijando_modo != key_modo:
                        fijando_modo = key_modo
                        inicio_fijacion = time.time()
                        if sonidos.get((1, 1)): sonidos[(1, 1)].play()
                    
                    t_transcurrido = time.time() - inicio_fijacion
                    t_restante = 3 - int(t_transcurrido)
                    
                    # Efecto de cargando (Cambiamos solo el borde a Amarillo)
                    cv2.rectangle(frame_flipped, (x1, y1), (x2, y2), (0, 255, 255), 4)
                    cv2.putText(frame_flipped, f"ELIGEN DO... {t_restante}s", (x1 + 10, y2 - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

                    if t_transcurrido >= 3:
                        modo_seleccionado = key_modo
                        # Animación de éxito final Verde
                        cv2.rectangle(frame_flipped, (x1, y1), (x2, y2), (0, 255, 0), cv2.FILLED)
                        cv2.putText(frame_flipped, "SELECCIONADO", (x1 + 10, y2 - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 0), 2)
                        cv2.imshow('Deteccion de Bateria', frame_flipped)
                        cv2.waitKey(1000)
                else:
                    cv2.rectangle(frame_flipped, (x1, y1), (x2, y2), (255, 0, 0), 3)
                    cv2.putText(frame_flipped, "Junta las baquetas", (x1 + 10, y2 - 15), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 255, 255), 1)

        if opcion_enfocada_actual != fijando_modo:
            fijando_modo = None

        cv2.putText(frame_flipped, "Elige tu Modo de Juego", (180, 40), cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 165, 255), 2)
        cv2.imshow('Deteccion de Bateria', frame_flipped)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            modo_seleccionado = "tradicional"
            break

    return modo_seleccionado

def fase4_ubicacion_personalizada(cap, sonidos, mode):
    """Pide al usuario que defina en donde colocar los tambores usando las dos baquetas."""
    pads_game = {(2, 1): (400, 450, 110, 30)}
    
    elementos = [
        ("Caja (Tarola)", (1, 1), 140, 50, "assets/images/snare.png", "assets/images/snare.png", 60, 25, 120, 100, [0, 255, 255]),
        ("Hi-Hat", (1, 0), 110, 30, "assets/images/hi-hat.png", "assets/images/hi-hat.png", 55, 15, 120, 60, [0, 0, 255]),
        ("Tom Superior", (0, 1), 100, 50, "assets/images/tom_alto.png", "assets/images/tom_alto.png", 50, 25, 110, 90, [255, 0, 0]),
        ("Tom Grave", (2, 2), 110, 30, "assets/images/tom_grave.png", "assets/images/tom_grave.png", 145, 35, 290, 240, [0, 165, 255]),
        ("Platillo", (0, 0), 110, 60, "assets/images/platillo.png", "assets/images/platillo.png", 55, 30, 120, 60, [0, 255, 0])
    ]
    if mode == "gamer":
        img_referencia = cv2.imread("assets/images/referencia_gamer.png", cv2.IMREAD_UNCHANGED)
    else:
        img_referencia = cv2.imread("assets/images/referencia_tradicional.png", cv2.IMREAD_UNCHANGED)
    
    if img_referencia is not None:
        if img_referencia.shape[2] == 3:
            img_referencia = cv2.cvtColor(img_referencia, cv2.COLOR_BGR2BGRA)
        # Ajusta este tamaño (180, 120) si la imagen es muy grande o muy pequeña
        if mode == "gamer":
            img_referencia = cv2.resize(img_referencia, (300, 220))
        else:
            img_referencia = cv2.resize(img_referencia, (250, 190))

    
    idx_elem = 0
    fijando = False
    inicio_fijacion = 0
    img_actual = None
    img_cargada_idx = -1
    
    # NUEVO: Lista para guardar las imágenes ya posicionadas y sus coordenadas
    imagenes_fijadas = []

    while idx_elem < len(elementos):
        ret, frame_raw = cap.read()
        if not ret: break
        
        # Desempaquetar configuración incluyendo el color_gamer
        nombre_elem, celda, bw, bh, ruta_trad, ruta_gam, eje_x, eje_y, img_w_scale, img_h_scale, color_gamer = elementos[idx_elem]

        # Cargar la imagen solo si el elemento cambió (para ahorrar recursos)
        if img_cargada_idx != idx_elem:
            ruta_usar = ruta_trad if mode == "tradicional" else ruta_gam
            img_actual = cv2.imread(ruta_usar, cv2.IMREAD_UNCHANGED)
            if img_actual is not None:
                if img_actual.shape[2] == 3:
                    img_actual = cv2.cvtColor(img_actual, cv2.COLOR_BGR2BGRA)
                
                img_actual = cv2.resize(img_actual, (img_w_scale, img_h_scale))

                # NUEVO: Rellenar la silueta con color sólido si estamos en modo gamer
                if mode == "gamer" and img_actual.shape[2] == 4:
                    # Sobrescribimos B(0), G(1), R(2) pero dejamos intacto el Alfa(3)
                    img_actual[:, :, 0] = color_gamer[0]
                    img_actual[:, :, 1] = color_gamer[1]
                    img_actual[:, :, 2] = color_gamer[2]
                
            img_cargada_idx = idx_elem

        frame_flipped = cv2.flip(frame_raw, 1)
        hsv = cv2.cvtColor(frame_flipped, cv2.COLOR_BGR2HSV)
        r1 = cv2.inRange(hsv, LOWER_RED1, UPPER_RED1)
        r2 = cv2.inRange(hsv, LOWER_RED2, UPPER_RED2)
        mask_green = r1 + r2  
        mask_red = cv2.inRange(hsv, LOWER_GREEN, UPPER_GREEN)

        kernel = np.ones((5,5), np.uint8)
        mask_r = cv2.morphologyEx(mask_red, cv2.MORPH_OPEN, kernel)
        mask_g = cv2.morphologyEx(mask_green, cv2.MORPH_OPEN, kernel)

        oscurecer_fondo(frame_flipped, opacidad=0.6)

        if img_referencia is not None:
            ref_x = FRAME_WIDTH // 2
            ref_y = FRAME_HEIGHT - 60  # Ajusta este '60' para subir o bajar la imagen
            frame_flipped = superponer_imagen_con_alfa(frame_flipped, img_referencia, ref_x, ref_y)


        frame_flipped[mask_r > 0] = [0, 0, 255]
        frame_flipped[mask_g > 0] = [0, 255, 0]

        # Dibujar Pads Previamente Guardados
        for _, (cx_pad, cy_pad, cw, ch) in pads_game.items():
            cv2.ellipse(frame_flipped, (cx_pad, cy_pad), (cw // 2, ch // 2), 0, 0, 360, (255, 255, 255), 2)

        # NUEVO: Dibujar imágenes de los elementos ya fijados
        for img_fijada, px, py in imagenes_fijadas:
            frame_flipped = superponer_imagen_con_alfa(frame_flipped, img_fijada, px, py)

        contornos_red, _ = cv2.findContours(mask_r, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        if contornos_red:
            c_red = max(contornos_red, key=cv2.contourArea)
            if cv2.contourArea(c_red) > 30:
                M = cv2.moments(c_red)
                if M["m00"] != 0:
                    cx, cy = int(M["m10"] / M["m00"]), int(M["m01"] / M["m00"])
                    
                    x1, y1 = max(0, cx - bw // 2), max(0, cy - bh // 2)
                    x2, y2 = min(FRAME_WIDTH, cx + bw // 2), min(FRAME_HEIGHT, cy + bh // 2)
                    
                    centro_img_x, centro_img_y = 0, 0
                    
                    if img_actual is not None:
                        img_h, img_w = img_actual.shape[:2]
                        centro_img_x = cx - eje_x + (img_w // 2)
                        centro_img_y = cy - eje_y + (img_h // 2)
                        frame_flipped = superponer_imagen_con_alfa(frame_flipped, img_actual, centro_img_x, centro_img_y)

                    roi_green = mask_g[y1:y2, x1:x2]
                    overlay = frame_flipped.copy()
                    
                    if cv2.countNonZero(roi_green) > 30:
                        if not fijando:
                            if sonidos.get((1, 1)): sonidos[(1, 1)].play()
                            fijando, inicio_fijacion = True, time.time()
                        
                        t_transcurrido = time.time() - inicio_fijacion
                        t_restante = 3 - int(t_transcurrido)

                        cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 255, 255), cv2.FILLED)
                        cv2.addWeighted(overlay, 0.4, frame_flipped, 0.6, 0, frame_flipped)
                        cv2.rectangle(frame_flipped, (x1, y1), (x2, y2), (0, 255, 255), 3)
                        cv2.putText(frame_flipped, f"FIJANDO... {t_restante}s", (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 255), 2)

                        if t_transcurrido >= 3:
                            pads_game[celda] = (cx, cy, bw, bh)
                            idx_elem += 1
                            fijando = False
                            
                            # NUEVO: Guardamos la imagen actual y las coordenadas finales para redibujarlas
                            if img_actual is not None:
                                imagenes_fijadas.append((img_actual.copy(), centro_img_x, centro_img_y))
                            
                            overlay_exito = frame_flipped.copy()
                            cv2.rectangle(overlay_exito, (x1, y1), (x2, y2), (0, 255, 0), cv2.FILLED)
                            cv2.addWeighted(overlay_exito, 0.6, frame_flipped, 0.4, 0, frame_flipped)
                            cv2.rectangle(frame_flipped, (x1, y1), (x2, y2), (0, 255, 0), 3)
                            cv2.imshow('Deteccion de Bateria', frame_flipped)
                            cv2.waitKey(600)
                    else:
                        fijando = False
                        cv2.rectangle(overlay, (x1, y1), (x2, y2), (255, 0, 0), cv2.FILLED)
                        cv2.addWeighted(overlay, 0.3, frame_flipped, 0.7, 0, frame_flipped)
                        cv2.rectangle(frame_flipped, (x1, y1), (x2, y2), (255, 0, 0), 2)
                        cv2.putText(frame_flipped, f"Acerca la baqueta verde aqui!", (30, 80), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)            
                else: fijando = False
            else: fijando = False
        else: fijando = False

        cv2.putText(frame_flipped, f"Ubicar Elementos: {idx_elem + 1}/5", (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 165, 255), 2)
        cv2.putText(frame_flipped, f"Actual: {nombre_elem}", (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.9, (0, 255, 0), 2)
        cv2.imshow('Deteccion de Bateria', frame_flipped)

        if cv2.waitKey(1) & 0xFF == ord('q'):
            pads_game = {
                (0, 0): (170, 70, 110, 60),  (0, 1): (400, 70, 90, 50),
                (1, 0): (190, 200, 110, 30), (1, 1): (320, 240, 140, 50), 
                (2, 2): (480, 240, 140, 50), (2, 1): (400, 450, 110, 30)
            }
            break

    return pads_game


def guardar_configuracion(pads_game):
    """Envía la configuración al middleware UDP y la guarda como JSON local."""
    lista = []
    for celda, (cx, cy, ancho, alto) in pads_game.items():
        lista.append({"elemento": str(celda), "x": cx, "y": cy, "w": ancho, "h": alto})

    mensaje = {
        "tipo": "configuracion",
        "dim_x": FRAME_WIDTH,
        "dim_y": FRAME_HEIGHT,
        "elementos": lista
    }

    # 1. Enviar UDP
    sock.sendto(json.dumps(mensaje).encode('utf-8'), (UDP_IP, UDP_PORT))

    # 2. Archivo Local
    with open("config_pads.json", "w") as f:
        json.dump(mensaje, f)

    print("Calibración guardada exitosamente en config_pads.json")


def main():
    sonidos = inicializar_audio()
    cap = inicializar_camara()

    fase1_calibracion_bombos(cap, sonidos)
    fase2_calibracion_posicionamiento(cap, sonidos)
    mode = fase3_eleccion_modo_de_juego(cap, sonidos)
    
    pads_finales = fase4_ubicacion_personalizada(cap, sonidos, mode)
    
    guardar_configuracion(pads_finales)

    cap.release()
    cv2.destroyAllWindows()
    pygame.quit()


if __name__ == "__main__":
    main()