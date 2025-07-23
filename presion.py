import time
import statistics
import RPi.GPIO as GPIO
import os

# ---------- Configuración de pines ----------
MOTOR_EN = 17
MOTOR_IN1 = 27
VALVULA_PIN = 23  # 2N2222, válvula normalmente abierta
HX710B_DOUT = 6
HX710B_SCK = 5

# ---------- Inicialización GPIO ----------
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)
GPIO.setup([MOTOR_EN, MOTOR_IN1, VALVULA_PIN, HX710B_SCK], GPIO.OUT)
GPIO.setup(HX710B_DOUT, GPIO.IN)
GPIO.output(MOTOR_EN, GPIO.LOW)
GPIO.output(MOTOR_IN1, GPIO.LOW)
GPIO.output(VALVULA_PIN, GPIO.HIGH)  # Cerrar válvula al inicio
GPIO.output(HX710B_SCK, GPIO.LOW)

# ---------- PWM para el motor ----------
pwm_motor = GPIO.PWM(MOTOR_EN, 100)  # 100 Hz
pwm_motor.start(0)  # Motor apagado al inicio

# ---------- Clase HX710B ----------
class HX710B:
    def __init__(self, dout_pin, sck_pin, offset=0, scale=10041.60):
        self.dout_pin = dout_pin
        self.sck_pin = sck_pin
        self.offset = offset
        self.scale = scale
        GPIO.setup(self.dout_pin, GPIO.IN)
        GPIO.setup(self.sck_pin, GPIO.OUT)
        GPIO.output(self.sck_pin, GPIO.LOW)

    def read_raw(self):
        timeout = time.time() + 1.0
        while GPIO.input(self.dout_pin) == GPIO.HIGH:
            if time.time() > timeout:
                return None
            time.sleep(0.001)
        data = 0
        for _ in range(24):
            GPIO.output(self.sck_pin, GPIO.HIGH)
            time.sleep(0.000001)
            data = data << 1
            GPIO.output(self.sck_pin, GPIO.LOW)
            if GPIO.input(self.dout_pin):
                data += 1
            time.sleep(0.000001)
        GPIO.output(self.sck_pin, GPIO.HIGH)
        time.sleep(0.000001)
        GPIO.output(self.sck_pin, GPIO.LOW)
        if data >= 0x800000:
            data -= 0x1000000
        return data

    def read_pressure_mmhg(self, samples=5):
        readings = []
        for _ in range(samples):
            raw = self.read_raw()
            if raw is not None:
                readings.append(raw)
            time.sleep(0.01)
        if readings:
            return (statistics.mean(readings) - self.offset) / self.scale
        return None

# ---------- Control de motor y válvula ----------
def motor_inflar(velocidad=100):
    GPIO.output(MOTOR_IN1, GPIO.HIGH)
    pwm_motor.ChangeDutyCycle(velocidad)  # velocidad de 0 a 100

def motor_parar():
    pwm_motor.ChangeDutyCycle(0)
    GPIO.output(MOTOR_IN1, GPIO.LOW)

def valvula_abrir():
    GPIO.output(VALVULA_PIN, GPIO.LOW)  # Válvula normalmente abierta, LOW = abierta

def valvula_cerrar():
    GPIO.output(VALVULA_PIN, GPIO.HIGH) # HIGH = cerrada

# ---------- Calibración del sensor HX710B ----------
def calibrar_sensor(sensor):
    print("\n--- Calibración del sensor HX710B ---")
    input("Asegúrate de que el manguito esté abierto al aire (sin presión). Presiona Enter para continuar...")
    lecturas_0 = []
    for _ in range(10):
        raw = sensor.read_raw()
        if raw is not None:
            lecturas_0.append(raw)
        time.sleep(0.05)
    offset = int(sum(lecturas_0) / len(lecturas_0)) if lecturas_0 else 0
    print(f"Valor crudo promedio a 0 mmHg: {offset}")
    input("Infla el manguito a una presión conocida (por ejemplo, 100 mmHg) y presiona Enter...")
    lecturas_100 = []
    for _ in range(10):
        raw = sensor.read_raw()
        if raw is not None:
            lecturas_100.append(raw)
        time.sleep(0.05)
    valor_100 = int(sum(lecturas_100) / len(lecturas_100)) if lecturas_100 else offset+10000
    print(f"Valor crudo promedio a presión conocida: {valor_100}")
    try:
        presion_real = float(input("¿Cuál es la presión real del manómetro de referencia (mmHg)? [100]: ").strip() or 100)
    except:
        presion_real = 100
    scale = (valor_100 - offset) / presion_real if presion_real != 0 else 1
    print(f"Offset sugerido: {offset}")
    print(f"Scale sugerido: {scale}")
    print("Copia estos valores en la clase HX710B para mejorar la precisión.")
    return offset, scale

# ---------- Medición automática ----------
def medir_presion_automatica(velocidad=100):
    sensor = HX710B(HX710B_DOUT, HX710B_SCK)
    # Descartar primeras lecturas anómalas
    print("Descartando primeras lecturas...")
    for _ in range(5):
        sensor.read_pressure_mmhg()
        time.sleep(0.02)
    print("\n--- Iniciando medición automática de presión arterial ---")
    valvula_cerrar()  # Asegura que la válvula esté cerrada
    print("Cerrando válvula y comenzando inflado")
    time.sleep(0.2)   # Pequeña pausa para asegurar el cierre
    motor_inflar(velocidad)
    presion_objetivo = 185
    presiones = []
    tiempos = []
    t0 = time.time()
    while True:
        presion = sensor.read_pressure_mmhg()
        t = time.time() - t0
        if presion:
            presiones.append(presion)
            tiempos.append(t)
            print(f"Presión: {presion:.1f} mmHg", end='\r')
            if presion >= presion_objetivo or presion > 200:
                break
        time.sleep(0.03)  # Mayor frecuencia de muestreo
    # --- Mantener presión objetivo 6 segundos ---
    print("\nPresión objetivo alcanzada. Manteniendo presión...")
    tiempo_mantener = 6  # Ahora mantiene la presión durante 6 segundos
    t_inicio_mantener = time.time()
    motor_inflar(velocidad)  # Mantener motor encendido
    valvula_cerrar()  # Asegurar válvula cerrada al inicio
    while time.time() - t_inicio_mantener < tiempo_mantener:
        presion = sensor.read_pressure_mmhg()
        t = time.time() - t0
        if presion:
            presiones.append(presion)
            tiempos.append(t)
            print(f"Presión (manteniendo): {presion:.1f} mmHg", end='\r')
            if presion > presion_objetivo + 2:
                motor_parar()
                valvula_abrir()
                time.sleep(0.05)
                valvula_cerrar()
            elif presion < presion_objetivo - 2:
                motor_inflar(velocidad)
                valvula_cerrar()
            else:
                motor_parar()
                valvula_cerrar()
        time.sleep(0.03)
    motor_parar()
    valvula_cerrar()  # Asegurar válvula cerrada antes de desinflar
    print("\nMantención finalizada. Desinflando lentamente...")
    # Desinflado controlado
    desinflando = True
    pulso_abierto = 0.01  # aún más corto, para evitar caídas bruscas en muñeca
    pulso_cerrado = 0.3   # más corto, para ciclos más frecuentes y controlados
    # SUGERENCIA: Para un desinflado más preciso, puedes:
    # 1. Hacer pulso_cerrado dependiente de la presión (más lento cerca de la diastólica)
    # 2. Medir la presión antes y después de cada pulso y ajustar la duración del pulso_abierto dinámicamente
    # 3. Implementar un bucle que abra la válvula solo lo necesario para bajar la presión 2-3 mmHg por ciclo
    # 4. Si tienes una válvula proporcional, usar PWM para abrirla parcialmente
    while desinflando:
        valvula_abrir()
        time.sleep(pulso_abierto)
        valvula_cerrar()
        time.sleep(pulso_cerrado)
        presion = sensor.read_pressure_mmhg()
        t = time.time() - t0
        if presion:
            presiones.append(presion)
            tiempos.append(t)
            print(f"Presión: {presion:.1f} mmHg", end='\r')
            if presion < 40:
                desinflando = False
        else:
            desinflando = False
    valvula_abrir()  # Liberar presión al final
    print("\nMedición finalizada. Procesando datos...")
    # Procesamiento oscilométrico básico
    sistolica, diastolica = detectar_sistolica_diastolica_oscilometrico(presiones)
    print(f"\n--- Resultados ---")
    print(f"Presión Sistólica estimada: {sistolica:.1f} mmHg" if sistolica else "No detectada")
    print(f"Presión Diastólica estimada: {diastolica:.1f} mmHg" if diastolica else "No detectada")

def medir_presion_automatica_pulsos(velocidad=100, pulso_motor=0.3, pausa_lectura=0.01):
    sensor = HX710B(HX710B_DOUT, HX710B_SCK)
    print("Descartando primeras lecturas...")
    for _ in range(5):
        sensor.read_pressure_mmhg()
        time.sleep(0.02)
    print("\n--- Iniciando medición automática de presión arterial (inflado por pulsos) ---")
    valvula_cerrar()
    print("Cerrando válvula y comenzando inflado por pulsos")
    time.sleep(0.2)
    presion_objetivo = 185
    presiones = []
    tiempos = []
    t0 = time.time()
    while True:
        motor_inflar(velocidad)
        time.sleep(pulso_motor)
        motor_parar()
        time.sleep(pausa_lectura)
        presion = sensor.read_pressure_mmhg()
        t = time.time() - t0
        if presion:
            presiones.append(presion)
            tiempos.append(t)
            print(f"Presión: {presion:.1f} mmHg", end='\r')
            if presion >= presion_objetivo or presion > 200:
                break
    # --- Mantener presión objetivo 6 segundos ---
    print("\nPresión objetivo alcanzada. Manteniendo presión...")
    tiempo_mantener = 6
    t_inicio_mantener = time.time()
    while time.time() - t_inicio_mantener < tiempo_mantener:
        presion = sensor.read_pressure_mmhg()
        t = time.time() - t0
        if presion:
            presiones.append(presion)
            tiempos.append(t)
            print(f"Presión (manteniendo): {presion:.1f} mmHg", end='\r')
        time.sleep(0.03)
    motor_parar()
    valvula_cerrar()
    print("\nMantención finalizada. Desinflando lentamente...")
    desinflando = True
    pulso_abierto = 0.01
    pulso_cerrado = 0.3
    while desinflando:
        valvula_abrir()
        time.sleep(pulso_abierto)
        valvula_cerrar()
        time.sleep(pulso_cerrado)
        presion = sensor.read_pressure_mmhg()
        t = time.time() - t0
        if presion:
            presiones.append(presion)
            tiempos.append(t)
            print(f"Presión: {presion:.1f} mmHg", end='\r')
            if presion < 40:
                desinflando = False
        else:
            desinflando = False
    valvula_abrir()
    print("\nMedición finalizada. Procesando datos...")
    sistolica, diastolica = detectar_sistolica_diastolica_oscilometrico(presiones)
    # Asegurar que sistólica > diastólica
    if sistolica and diastolica and sistolica < diastolica:
        sistolica, diastolica = diastolica, sistolica
    print(f"\n--- Resultados ---")
    print(f"Presión Sistólica estimada: {sistolica:.1f} mmHg" if sistolica else "No detectada")
    print(f"Presión Diastólica estimada: {diastolica:.1f} mmHg" if diastolica else "No detectada")


# ---------- Filtro de media móvil ----------
def media_movil(datos, ventana=5):
    if len(datos) < ventana:
        return datos
    return [statistics.mean(datos[max(0, i-ventana+1):i+1]) for i in range(len(datos))]

# ---------- Algoritmo oscilométrico mejorado ----------
def detectar_sistolica_diastolica_oscilometrico(presiones):
    if len(presiones) < 10:
        print("No hay suficientes datos para análisis oscilométrico.")
        return None, None
    # 1. Suavizar la señal
    presiones_suavizadas = media_movil(presiones, ventana=5)
    # 2. Calcular diferencias (oscilaciones)
    diffs = [abs(presiones_suavizadas[i] - presiones_suavizadas[i-1]) for i in range(1, len(presiones_suavizadas))]
    print("\nPresiones (suavizadas):", [f"{p:.1f}" for p in presiones_suavizadas])
    print("Oscilaciones (diffs):", [f"{d:.2f}" for d in diffs])
    max_osc = max(diffs)
    idx_max = diffs.index(max_osc) + 1
    resultados = {}
    for umbral_pct in [0.4, 0.5, 0.6]:
        umbral = umbral_pct * max_osc
        sistolica = None
        diastolica = None
        # Buscar sistólica (antes del máximo)
        for i in range(1, idx_max):
            if diffs[i] > umbral:
                sistolica = presiones_suavizadas[i]
                break
        # Buscar diastólica (después del máximo)
        for i in range(idx_max, len(diffs)):
            if diffs[i] < umbral:
                diastolica = presiones_suavizadas[i]
                break
        resultados[umbral_pct] = (sistolica, diastolica)
    print("\nResultados para diferentes umbrales:")
    for pct, (sis, dias) in resultados.items():
        print(f"  Umbral {int(pct*100)}%: Sistólica={sis:.1f} mmHg, Diastólica={dias:.1f} mmHg" if sis and dias else f"  Umbral {int(pct*100)}%: No detectada")
    # Devuelve el resultado para 50% (clásico)
    return resultados[0.5]

def prueba_valvula(repeticiones=10, tiempo_on=1, tiempo_off=1):
    print(f"\n--- Prueba de válvula: {repeticiones} ciclos ---")
    for i in range(repeticiones):
        print(f"Ciclo {i+1}: Abriendo válvula")
        valvula_abrir()
        time.sleep(tiempo_on)
        print(f"Ciclo {i+1}: Cerrando válvula")
        valvula_cerrar()
        time.sleep(tiempo_off)
    valvula_cerrar()
    print("Prueba de válvula finalizada.")

# ---------- Main ----------
if __name__ == "__main__":
    try:
        print("Seleccione una opción:")
        print("1. Medir presión automática")
        print("2. Prueba de válvula (abrir/cerrar repetido)")
        print("3. Calibrar sensor HX710B")
        print("4. Medir presión automática (inflado por pulsos)")
        opcion = input("Opción [1/2]: ").strip()
        if opcion == "2":
            rep = input("¿Cuántos ciclos? [10]: ").strip()
            t_on = input("¿Tiempo abierta (s)? [1]: ").strip()
            t_off = input("¿Tiempo cerrada (s)? [1]: ").strip()
            rep = int(rep) if rep else 10
            t_on = float(t_on) if t_on else 1
            t_off = float(t_off) if t_off else 1
            prueba_valvula(rep, t_on, t_off)
        elif opcion == "3":
            sensor = HX710B(HX710B_DOUT, HX710B_SCK)
            calibrar_sensor(sensor)
        elif opcion == "4":
            vel = input("¿Velocidad de inflado? [100]: ").strip()
            vel = int(vel) if vel else 100
            medir_presion_automatica_pulsos(vel)
        else:
            vel = input("¿Velocidad de inflado? [100]: ").strip()
            vel = int(vel) if vel else 100
            medir_presion_automatica(vel)
    except KeyboardInterrupt:
        print("Interrumpido por usuario.")
    finally:
        motor_parar()
        valvula_abrir()
        pwm_motor.stop()
        GPIO.cleanup()
        print("GPIO liberado.")