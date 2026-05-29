import cv2

def listar_camaras_disponibles():
    camaras_validas = []
    # Probamos los índices del 0 al 4
    for i in range(5):
        cap = cv2.VideoCapture(i, cv2.CAP_DSHOW) # CAP_DSHOW ayuda a que abra más rápido en Windows
        if cap.isOpened():
            ret, _ = cap.read()
            if ret:
                camaras_validas.append(i)
        cap.release()
    
    return camaras_validas

camaras = listar_camaras_disponibles()
print(f"Cámaras encontradas en los índices: {camaras}")