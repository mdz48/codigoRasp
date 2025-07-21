import time
import statistics
import RPi.GPIO as GPIO

# ---------- Configuración GPIO ----------
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Factor de calibración (ajusta este valor según tus pruebas)
SCALE_FACTOR = 1853.65

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
        self.offset = 0
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
        print("Calibrando en presión atmosférica... No conecte brazalete.")
        readings = []
        for _ in range(20):
            raw = self.read_raw()
            if raw:
                readings.append(raw)
            time.sleep(0.1)
        if readings:
            self.offset = statistics.mean(readings)
            print(f"Offset calibrado: {self.offset}")
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

# ---------- Medición ----------
def medir_presion():
    sensor = HX710B(HX710B_DOUT, HX710B_SCK)
    print("\n--- Calibración del sensor (solo offset) ---")
    print("1. Retire el brazalete y asegúrese de que el sensor esté a presión atmosférica.")
    input("Presione Enter para calibrar el offset...")
    sensor.calibrate_offset()

    input("\nColoque el brazalete en el paciente y presione Enter para inflar...")
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

# ---------- Main ----------
if __name__ == "__main__":
    try:
        medir_presion()
    except KeyboardInterrupt:
        print("Interrumpido por usuario.")
    finally:
        motor_parar()
        GPIO.cleanup()
        print("GPIO liberado.")