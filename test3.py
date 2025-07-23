import time
import statistics
import RPi.GPIO as GPIO
import os

# ---------- Manejo de calibración persistente ----------
CALIB_FILE = "calibracion.txt"
def guardar_calibracion(offset, scale):
    with open(CALIB_FILE, 'w') as f:
        f.write(f"offset={offset}\n")
        f.write(f"scale={scale}\n")

def cargar_calibracion():
    offset = 0
    scale = 10041.60
    if os.path.exists(CALIB_FILE):
        with open(CALIB_FILE, 'r') as f:
            for line in f:
                if line.startswith('offset='):
                    try:
                        offset = float(line.strip().split('=')[1])
                    except:
                        pass
                elif line.startswith('scale='):
                    try:
                        scale = float(line.strip().split('=')[1])
                    except:
                        pass
    return offset, scale

# ---------- Configuración GPIO ----------
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Cargar calibración persistente
OFFSET, SCALE_FACTOR = cargar_calibracion()

# Pines para HX710B (sensor de presión)
HX710B_DOUT = 6
HX710B_SCK = 5
GPIO.setup(HX710B_DOUT, GPIO.IN)
GPIO.setup(HX710B_SCK, GPIO.OUT)
GPIO.output(HX710B_SCK, GPIO.LOW)

# Pines para motor
MOTOR_EN = 17
MOTOR_IN1 = 27
GPIO.setup([MOTOR_EN, MOTOR_IN1], GPIO.OUT)
GPIO.output(MOTOR_EN, GPIO.LOW)
GPIO.output(MOTOR_IN1, GPIO.LOW)

# ---------- Clase HX710B ----------
class HX710B:
    def __init__(self, dout_pin, sck_pin):
        self.dout_pin = dout_pin
        self.sck_pin = sck_pin
        self.offset = OFFSET
        self.scale = SCALE_FACTOR
        GPIO.setup(self.dout_pin, GPIO.IN)
        GPIO.setup(self.sck_pin, GPIO.OUT)
        GPIO.output(self.sck_pin, GPIO.LOW)

    def read_raw(self):
        timeout = time.time() + 1.0
        while GPIO.input(self.dout_pin) == GPIO.HIGH:
            if time.time() > timeout:
                print("Timeout esperando datos del sensor")
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

    def calibrate_offset(self):
        print("\n[Calibración] Retire el brazalete y asegúrese de que el sensor esté a presión atmosférica.")
        input("Presione Enter para calibrar el offset...")
        readings = []
        for _ in range(20):
            raw = self.read_raw()
            if raw:
                readings.append(raw)
            time.sleep(0.1)
        if readings:
            self.offset = statistics.mean(readings)
            print(f"Offset calibrado: {self.offset}")
            # Guardar offset y scale actual
            guardar_calibracion(self.offset, self.scale)
        else:
            print("Fallo al calibrar offset")

# ---------- Control del motor ----------
def motor_inflar():
    GPIO.output(MOTOR_IN1, GPIO.HIGH)
    GPIO.output(MOTOR_EN, GPIO.HIGH)

def motor_parar():
    GPIO.output(MOTOR_EN, GPIO.LOW)
    GPIO.output(MOTOR_IN1, GPIO.LOW)

# ---------- Procesamiento de picos (simplificado) ----------
def detectar_presion_sistolica_diastolica(presiones):
    delta = [abs(presiones[i] - presiones[i-1]) for i in range(1, len(presiones))]
    umbral = statistics.mean(delta) * 1.5

    sistolica = None
    diastolica = None
    for i in range(1, len(delta)):
        if delta[i] > umbral and not sistolica:
            sistolica = presiones[i]
        elif delta[i] < umbral and sistolica and not diastolica:
            diastolica = presiones[i]
            break
    return sistolica, diastolica

# ---------- Medición completa ----------
def medir_presion(sensor):
    print("\n--- Medición completa ---")
    input("Coloque el brazalete en el paciente y presione Enter para inflar...")
    print("Inflando...")
    motor_inflar()
    presion_objetivo = 200
    presiones_inflado = []
    while True:
        presion = sensor.read_pressure_mmhg()
        if presion:
            presiones_inflado.append(presion)
            print(f"Presión: {presion:.1f} mmHg")
            if presion >= presion_objetivo or presion > 220:
                break
        time.sleep(0.1)
    motor_parar()
    print(f"Presión objetivo alcanzada ({presion_objetivo} mmHg). Manteniendo presión durante 5 segundos...")
    tiempo_inicio = time.time()
    while time.time() - tiempo_inicio < 5:
        presion = sensor.read_pressure_mmhg()
        if presion:
            print(f"Presión mantenida: {presion:.1f} mmHg")
        time.sleep(0.5)

    print("Registrando desinflado (4 segundos)...")
    presiones_desinflado = []
    tiempos = []
    inicio = time.time()
    while time.time() - inicio < 4:
        presion = sensor.read_pressure_mmhg()
        if presion:
            presiones_desinflado.append(presion)
            tiempos.append(time.time() - inicio)
            print(f"Presión: {presion:.1f} mmHg")
        time.sleep(0.1)

    # Sistólica: máxima durante inflado, Diastólica: mínima durante desinflado
    sistolica = max(presiones_inflado) if presiones_inflado else None
    diastolica = min(presiones_desinflado) if presiones_desinflado else None

    print("\n--- Resultados ---")
    print(f"Presión Sistólica estimada: {sistolica:.1f} mmHg" if sistolica else "No se detectó presión sistólica")
    print(f"Presión Diastólica estimada: {diastolica:.1f} mmHg" if diastolica else "No se detectó presión diastólica")

    # --- Calibración automática del SCALE_FACTOR ---
    if sistolica is not None:
        resp = input("\n¿Deseas calibrar el SCALE_FACTOR usando el valor máximo detectado? (s/n): ").strip().lower()
        if resp == 's':
            try:
                valor_real = float(input("Ingresa el valor real de la presión sistólica (mmHg) según tu manómetro: "))
            except ValueError:
                print("Valor inválido. No se calibró el factor de escala.")
                return
            # Obtener el valor crudo promedio correspondiente al pico sistólico
            lecturas_crudas = []
            for _ in range(10):
                raw = sensor.read_raw()
                if raw is not None:
                    lecturas_crudas.append(raw)
                time.sleep(0.05)
            if not lecturas_crudas:
                print("No se pudieron obtener lecturas crudas para calibrar.")
                return
            promedio_crudo = statistics.mean(lecturas_crudas)
            nuevo_scale = (promedio_crudo - sensor.offset) / valor_real if valor_real != 0 else None
            if nuevo_scale and nuevo_scale > 0:
                print(f"\nNuevo SCALE_FACTOR calculado: {nuevo_scale:.2f}")
                print("Copia este valor y reemplázalo en el código para futuras mediciones.")
                # Guardar el nuevo scale y offset actual
                guardar_calibracion(sensor.offset, nuevo_scale)
                sensor.scale = nuevo_scale
            else:
                print("No se pudo calcular un factor de escala válido. Verifica los datos e inténtalo de nuevo.")

# ---------- Menú interactivo ----------
def menu():
    sensor = HX710B(HX710B_DOUT, HX710B_SCK)
    global SCALE_FACTOR
    while True:
        print("\n--- Menú de Calibración y Prueba del Sensor de Presión ---")
        print("1. Calibrar offset (presión atmosférica)")
        print("2. Leer valor crudo del sensor")
        print("3. Leer presión en mmHg")
        print("4. Medición completa (inflar/desinflar)")
        print("5. Salir")
        print("6. Calibrar factor de escala (SCALE_FACTOR) automáticamente")
        opcion = input("Seleccione una opción: ")
        if opcion == "1":
            sensor.calibrate_offset()
        elif opcion == "2":
            raw = sensor.read_raw()
            print(f"Valor crudo leído: {raw}")
        elif opcion == "3":
            presion = sensor.read_pressure_mmhg()
            print(f"Presión estimada: {presion:.2f} mmHg" if presion is not None else "No se pudo leer presión")
        elif opcion == "4":
            medir_presion(sensor)
        elif opcion == "5":
            print("Saliendo...")
            break
        elif opcion == "6":
            print("\n[Calibración de factor de escala]")
            print("Asegúrese de que el offset ya esté calibrado (opción 1).\n")
            input("Aplique una presión conocida al sensor y presione Enter cuando esté estable...")
            try:
                valor_real = float(input("Ingrese el valor real de la presión aplicada (mmHg): "))
            except ValueError:
                print("Valor inválido. Intente de nuevo.")
                continue
            lecturas = []
            for i in range(10):
                raw = sensor.read_raw()
                if raw is not None:
                    lecturas.append(raw)
                time.sleep(0.1)
            if not lecturas:
                print("No se pudieron obtener lecturas crudas. Intente de nuevo.")
                continue
            promedio_crudo = statistics.mean(lecturas)
            nuevo_scale = (promedio_crudo - sensor.offset) / valor_real if valor_real != 0 else None
            if nuevo_scale and nuevo_scale > 0:
                print(f"\nNuevo SCALE_FACTOR calculado: {nuevo_scale:.2f}")
                print("Copia este valor y reemplázalo en el código para futuras mediciones.")
                SCALE_FACTOR = nuevo_scale
                sensor.scale = nuevo_scale
            else:
                print("No se pudo calcular un factor de escala válido. Verifica los datos e inténtalo de nuevo.")
        else:
            print("Opción no válida. Intente de nuevo.")

# ---------- Main ----------
if __name__ == "__main__":
    try:
        menu()
    except KeyboardInterrupt:
        print("Interrumpido por usuario.")
    finally:
        motor_parar()
        GPIO.cleanup()
        print("GPIO liberado.")