import time
import json
import RPi.GPIO as GPIO
import threading
from datetime import datetime
import numpy as np
from scipy.signal import find_peaks
import paho.mqtt.client as mqtt
import pika

# ---------- Configuración GPIO ----------
GPIO.setmode(GPIO.BCM)
GPIO.setwarnings(False)

# Pines para HX710B (sensor de presión) - CORREGIDO PARA TU MÓDULO
HX710B_OUT = 6  # GPIO 6 - Data (OUT en tu módulo)
HX710B_SCK = 5  # GPIO 5 - Clock

# Pines para L293D (control de motor y válvula)
# Canal A - Motor de inflado
MOTOR_EN = 17  # GPIO 17 - Enable canal A
MOTOR_IN1 = 27  # GPIO 27 - Control dirección motor
MOTOR_IN2 = 22  # GPIO 22 - Control dirección motor (alternativo)

# Canal B - Válvula
VALVE_EN = 23  # GPIO 23 - Enable canal B
VALVE_IN1 = 24  # GPIO 24 - Control válvula
VALVE_IN2 = 25  # GPIO 25 - Control válvula (alternativo)

# Configurar pines
GPIO.setup(HX710B_OUT, GPIO.IN, pull_up_down=GPIO.PUD_UP)  # CAMBIADO A OUT
GPIO.setup(HX710B_SCK, GPIO.OUT)
GPIO.setup(MOTOR_EN, GPIO.OUT)
GPIO.setup(MOTOR_IN1, GPIO.OUT)
GPIO.setup(MOTOR_IN2, GPIO.OUT)
GPIO.setup(VALVE_EN, GPIO.OUT)
GPIO.setup(VALVE_IN1, GPIO.OUT)
GPIO.setup(VALVE_IN2, GPIO.OUT)

# Inicializar PWM para control de velocidad
motor_pwm = GPIO.PWM(MOTOR_EN, 1000)
valve_pwm = GPIO.PWM(VALVE_EN, 1000)
motor_pwm.start(0)
valve_pwm.start(0)

# ---------- Configuración MQTT/RabbitMQ ----------
RABBITMQ_HOST = "100.28.59.47"
RABBITMQ_USER = "admin"
RABBITMQ_PASSWORD = "password"
EXCHANGE = 'amq.topic'

mqtt_host = "100.28.59.47"
mqtt_port = 1883
mqtt_user = "admin"
mqtt_password = "password"
mqtt_topic_bp = "presion_arterial"

# ---------- Variables globales ----------
current_patient_id = None
current_doctor_id = None
calibration_factor = 1.0  # Factor de calibración del sensor
baseline_pressure = 0  # Presión de referencia
is_measuring = False
pressure_readings = []
systolic_pressure = 0
diastolic_pressure = 0
mean_pressure = 0
heart_rate = 0

# ---------- Clase HX710B CORREGIDA PARA TU MÓDULO ----------
class HX710B:
    def __init__(self, out_pin, sck_pin):  # CAMBIADO A OUT_PIN
        self.out_pin = out_pin  # CAMBIADO A OUT_PIN
        self.sck_pin = sck_pin
        self.offset = 0
        self.scale = 1.0
        self.last_raw = 0
        
    def read_raw(self):
        """Lee el valor raw del sensor HX710B - VERSIÓN CORREGIDA PARA TU MÓDULO"""
        # Esperar a que el sensor esté listo (OUT en HIGH)
        timeout = 0
        while GPIO.input(self.out_pin) == GPIO.LOW and timeout < 1000:  # CAMBIADO A OUT_PIN
            time.sleep(0.0001)
            timeout += 1
            
        if timeout >= 1000:
            print("Error: Sensor HX710B no responde")
            return self.last_raw
            
        # Leer 24 bits
        count = 0
        for i in range(24):
            GPIO.output(self.sck_pin, GPIO.HIGH)
            time.sleep(0.0001)  # Tiempo de estabilización
            count = count << 1
            GPIO.output(self.sck_pin, GPIO.LOW)
            time.sleep(0.0001)
            if GPIO.input(self.out_pin):  # CAMBIADO A OUT_PIN
                count += 1
                
        # Pulso adicional para el siguiente ciclo
        GPIO.output(self.sck_pin, GPIO.HIGH)
        time.sleep(0.0001)
        GPIO.output(self.sck_pin, GPIO.LOW)
        time.sleep(0.0001)
        
        # Convertir a valor con signo (24-bit signed)
        if count & 0x800000:
            count -= 0x1000000
            
        self.last_raw = count
        return count
    
    def read_pressure(self):
        """Lee la presión en mmHg - VERSIÓN MEJORADA PARA TU MÓDULO"""
        raw_value = self.read_raw()
        
        # Factor de conversión mejorado para mmHg
        # Ajustar según tu sensor específico
        pressure = (raw_value - self.offset) * self.scale * 0.001  # Factor de escala
        
        return pressure
    
    def calibrate(self, known_pressure=0):
        """Calibra el sensor con una presión conocida - VERSIÓN MEJORADA"""
        print("Iniciando calibración del sensor HX710B...")
        print("Asegúrate de que el sensor esté conectado:")
        print("- VCC → 3.3V")
        print("- GND → GND") 
        print("- OUT → GPIO 6")
        print("- SCK → GPIO 5")
        
        # Tomar múltiples lecturas para estabilizar
        readings = []
        for i in range(20):
            raw_val = self.read_raw()
            readings.append(raw_val)
            print(f"Lectura {i+1}: {raw_val}")
            time.sleep(0.1)
        
        self.offset = np.mean(readings)
        print(f"Offset calculado: {self.offset}")
        
        # Si se proporciona una presión conocida, calcular escala
        if known_pressure != 0:
            self.scale = known_pressure / (np.mean(readings) - self.offset)
            print(f"Escala calculada: {self.scale}")
        else:
            # Escala por defecto para mmHg - AJUSTAR SEGÚN TU SENSOR
            self.scale = 0.001  # Ajustar este valor según tu sensor
            print(f"Usando escala por defecto: {self.scale}")
        
        print("Calibración completada")

# ---------- Control de motor y válvula MEJORADO ----------
class PressureController:
    def __init__(self):
        self.target_pressure = 0
        self.current_pressure = 0
        self.is_inflating = False
        self.is_deflating = False
        self.valve_is_open = False
        
    def inflate(self, speed=100):  # AUMENTADO A 100%
        """Infla el brazalete con máxima potencia"""
        print(f"Iniciando inflado con velocidad: {speed}%")
        
        # Cerrar válvula primero para evitar fugas
        self.close_valve()
        
        # Configurar dirección del motor
        GPIO.output(MOTOR_IN1, GPIO.HIGH)
        GPIO.output(MOTOR_IN2, GPIO.LOW)
        
        # Aplicar PWM con velocidad máxima
        motor_pwm.ChangeDutyCycle(speed)
        
        self.is_inflating = True
        self.is_deflating = False
        
    def deflate(self, speed=30):  # REDUCIDO A 30% PARA CONTROL MÁS PRECISO
        """Desinfla el brazalete con control preciso"""
        print(f"Iniciando desinflado controlado con velocidad: {speed}%")
        
        # Detener motor primero
        motor_pwm.ChangeDutyCycle(0)
        GPIO.output(MOTOR_IN1, GPIO.LOW)
        GPIO.output(MOTOR_IN2, GPIO.LOW)
        
        # Configurar válvula con control preciso
        GPIO.output(VALVE_IN1, GPIO.HIGH)
        GPIO.output(VALVE_IN2, GPIO.LOW)
        
        # Aplicar PWM con velocidad controlada
        valve_pwm.ChangeDutyCycle(speed)
        
        self.is_inflating = False
        self.is_deflating = True
        self.valve_is_open = True
        
    def close_valve(self):
        """Cierra la válvula completamente"""
        if self.valve_is_open:
            print("Cerrando válvula...")
            valve_pwm.ChangeDutyCycle(0)
            GPIO.output(VALVE_IN1, GPIO.LOW)
            GPIO.output(VALVE_IN2, GPIO.LOW)
            self.valve_is_open = False
            time.sleep(0.1)  # Pequeña pausa para estabilizar
        
    def stop(self):
        """Detiene motor y válvula de forma segura"""
        print("Deteniendo motor y válvula...")
        
        # Detener motor
        motor_pwm.ChangeDutyCycle(0)
        GPIO.output(MOTOR_IN1, GPIO.LOW)
        GPIO.output(MOTOR_IN2, GPIO.LOW)
        
        # Cerrar válvula de forma segura
        self.close_valve()
        
        self.is_inflating = False
        self.is_deflating = False
        print("Motor y válvula detenidos correctamente")

# ---------- Algoritmo de medición de presión arterial MEJORADO ----------
class BloodPressureMonitor:
    def __init__(self):
        self.sensor = HX710B(HX710B_OUT, HX710B_SCK)  # CAMBIADO A OUT
        self.controller = PressureController()
        self.measurement_data = []
        
    def calibrate_sensor(self):
        """Calibra el sensor de presión - VERSIÓN MEJORADA"""
        print("=== CALIBRACIÓN DEL SENSOR ===")
        print("1. Asegúrate de que el brazalete esté completamente desinflado")
        print("2. El sensor debe estar conectado correctamente:")
        print("   - VCC → 3.3V")
        print("   - GND → GND")
        print("   - OUT → GPIO 6")
        print("   - SCK → GPIO 5")
        print("3. Presiona Enter cuando estés listo")
        input()
        
        # Calibrar con presión atmosférica
        self.sensor.calibrate(0)
        
        # Probar lectura
        print("\nProbando lectura del sensor...")
        for i in range(5):
            pressure = self.sensor.read_pressure()
            raw = self.sensor.read_raw()
            print(f"Lectura {i+1}: Raw={raw}, Presión={pressure:.2f} mmHg")
            time.sleep(0.5)
        
        print("Calibración completada")
        
    def test_sensor(self):
        """Prueba el sensor para verificar que funciona"""
        print("=== PRUEBA DEL SENSOR ===")
        print("Realizando 10 lecturas del sensor...")
        
        readings = []
        for i in range(10):
            raw = self.sensor.read_raw()
            pressure = self.sensor.read_pressure()
            readings.append(raw)
            print(f"Lectura {i+1}: Raw={raw}, Presión={pressure:.2f} mmHg")
            time.sleep(0.2)
        
        print(f"\nEstadísticas:")
        print(f"Valor mínimo: {min(readings)}")
        print(f"Valor máximo: {max(readings)}")
        print(f"Promedio: {np.mean(readings):.2f}")
        print(f"Desviación estándar: {np.std(readings):.2f}")
        
        if np.std(readings) < 100:
            print("⚠️  ADVERTENCIA: El sensor parece no estar respondiendo correctamente")
            print("Verifica las conexiones:")
            print("- OUT (GPIO 6) conectado correctamente")
            print("- SCK (GPIO 5) conectado correctamente")
            print("- VCC conectado a 3.3V")
            print("- GND conectado a tierra")
        else:
            print("✅ Sensor funcionando correctamente")
        
    def measure_blood_pressure(self):
        """Realiza la medición completa de presión arterial - VERSIÓN MEJORADA"""
        global is_measuring, pressure_readings, systolic_pressure, diastolic_pressure, mean_pressure, heart_rate
        
        is_measuring = True
        pressure_readings = []
        
        print("=== INICIANDO MEDICIÓN DE PRESIÓN ARTERIAL ===")
        print("1. Inflando brazalete con máxima potencia...")
        
        # Fase 1: Inflar hasta presión sistólica estimada
        target_pressure = 180  # mmHg
        self.controller.inflate(100)  # MÁXIMA POTENCIA
        
        start_time = time.time()
        inflation_time = 0
        
        while self.sensor.read_pressure() < target_pressure and inflation_time < 30:
            current_pressure = self.sensor.read_pressure()
            pressure_readings.append({
                'pressure': current_pressure,
                'timestamp': time.time()
            })
            print(f"Presión actual: {current_pressure:.1f} mmHg")
            time.sleep(0.1)
            inflation_time = time.time() - start_time
            
        self.controller.stop()
        print(f"Brazalete inflado. Tiempo de inflado: {inflation_time:.1f} segundos")
        
        if current_pressure < 100:
            print("⚠️  ADVERTENCIA: Presión baja detectada. Verificar:")
            print("- Conexiones del motor")
            print("- Alimentación del L293D")
            print("- Brazalete correctamente conectado")
        
        print("2. Iniciando deflación controlada...")
        
        # Fase 2: Deflación controlada y detección de pulsos
        self.controller.deflate(50)  # MÁS VELOCIDAD
        pulse_detected = False
        systolic_found = False
        diastolic_found = False
        
        while self.sensor.read_pressure() > 50 and not diastolic_found:
            current_pressure = self.sensor.read_pressure()
            pressure_readings.append({
                'pressure': current_pressure,
                'timestamp': time.time()
            })
            
            # Detectar pulsos usando análisis de señal
            if len(pressure_readings) > 50:
                pressures = [p['pressure'] for p in pressure_readings[-50:]]
                peaks, _ = find_peaks(pressures, height=np.mean(pressures) + np.std(pressures))
                
                if len(peaks) > 0 and not systolic_found:
                    systolic_pressure = current_pressure
                    systolic_found = True
                    print(f"✅ Presión sistólica detectada: {systolic_pressure:.1f} mmHg")
                    
                elif len(peaks) == 0 and systolic_found and not diastolic_found:
                    diastolic_pressure = current_pressure
                    diastolic_found = True
                    print(f"✅ Presión diastólica detectada: {diastolic_pressure:.1f} mmHg")
            
            time.sleep(0.05)
            
        # Cerrar válvula de forma segura al final
        self.controller.close_valve()
        self.controller.stop()
        is_measuring = False
        
        # Calcular presión media y frecuencia cardíaca
        if systolic_found and diastolic_found:
            mean_pressure = diastolic_pressure + (systolic_pressure - diastolic_pressure) / 3
            
            # Calcular frecuencia cardíaca
            if len(pressure_readings) > 100:
                pressures = [p['pressure'] for p in pressure_readings]
                peaks, _ = find_peaks(pressures, height=np.mean(pressures) + np.std(pressures))
                if len(peaks) > 1:
                    time_diff = pressure_readings[peaks[-1]]['timestamp'] - pressure_readings[peaks[0]]['timestamp']
                    heart_rate = (len(peaks) - 1) * 60 / time_diff if time_diff > 0 else 0
                    
            return {
                'systolic': systolic_pressure,
                'diastolic': diastolic_pressure,
                'mean': mean_pressure,
                'heart_rate': heart_rate,
                'timestamp': datetime.now().isoformat()
            }
        else:
            print("❌ Error: No se pudieron detectar las presiones")
            # Cerrar válvula incluso si hay error
            self.controller.close_valve()
            return None

# ---------- Funciones de comunicación ----------
def send_to_rabbitmq(topic, data):
    """Envía datos a RabbitMQ"""
    try:
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD)
        parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials)
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        
        message = json.dumps(data)
        channel.basic_publish(
            exchange=EXCHANGE,
            routing_key=topic,
            body=message,
            properties=pika.BasicProperties(delivery_mode=2)
        )
        print(f"Datos enviados a RabbitMQ - {topic}: {message}")
        
        connection.close()
    except Exception as e:
        print(f"Error enviando a RabbitMQ: {e}")

def send_blood_pressure_data(measurement_data):
    """Envía los datos de presión arterial"""
    if measurement_data:
        # Datos para RabbitMQ
        rabbitmq_data = {
            "patient_id": current_patient_id,
            "doctor_id": current_doctor_id,
            "systolic": measurement_data['systolic'],
            "diastolic": measurement_data['diastolic'],
            "mean_pressure": measurement_data['mean'],
            "heart_rate": measurement_data['heart_rate'],
            "timestamp": measurement_data['timestamp']
        }
        
        send_to_rabbitmq("presion_arterial", rabbitmq_data)
        
        # Enviar a MQTT también
        mqtt_data = json.dumps(rabbitmq_data)
        client.publish(mqtt_topic_bp, mqtt_data)
        print("Datos de presión arterial enviados:", mqtt_data)

# ---------- Configuración MQTT ----------
client = mqtt.Client()
client.username_pw_set(mqtt_user, mqtt_password)
client.connect(mqtt_host, mqtt_port, keepalive=60)
client.loop_start()

# ---------- Función principal MEJORADA ----------
def main():
    global current_patient_id, current_doctor_id
    
    print("=== Medidor de Presión Arterial ===")
    print("Sistema: HX710B + L293D + Raspberry Pi")
    print("Conectando sensores...")
    
    # Inicializar monitor
    monitor = BloodPressureMonitor()
    
    # Calibrar sensor
    monitor.calibrate_sensor()
    
    try:
        while True:
            print("\n" + "="*50)
            print("1. Realizar medición de presión arterial")
            print("2. Calibrar sensor")
            print("3. Probar sensor")
            print("4. Ver estado del sistema")
            print("5. Salir")
            
            choice = input("Selecciona una opción: ")
            
            if choice == "1":
                if is_measuring:
                    print("Ya hay una medición en curso...")
                else:
                    print("Preparando medición...")
                    print("Coloca el brazalete en el brazo y presiona Enter")
                    input()
                    
                    # Configurar ID del paciente (puedes modificar esto)
                    current_patient_id = input("Ingresa el ID del paciente (o Enter para usar default): ") or "PAC001"
                    current_doctor_id = input("Ingresa el ID del doctor (opcional): ") or None
                    
                    measurement = monitor.measure_blood_pressure()
                    if measurement:
                        print("\n" + "="*50)
                        print("RESULTADOS DE LA MEDICIÓN:")
                        print(f"Presión Sistólica: {measurement['systolic']:.1f} mmHg")
                        print(f"Presión Diastólica: {measurement['diastolic']:.1f} mmHg")
                        print(f"Presión Media: {measurement['mean']:.1f} mmHg")
                        print(f"Frecuencia Cardíaca: {measurement['heart_rate']:.1f} bpm")
                        print("="*50)
                        
                        # Clasificación de presión arterial
                        if measurement['systolic'] < 120 and measurement['diastolic'] < 80:
                            print("Clasificación: NORMAL")
                        elif measurement['systolic'] < 130 and measurement['diastolic'] < 80:
                            print("Clasificación: ELEVADA")
                        elif measurement['systolic'] < 140 and measurement['diastolic'] < 90:
                            print("Clasificación: HIPERTENSIÓN ESTADIO 1")
                        else:
                            print("Clasificación: HIPERTENSIÓN ESTADIO 2")
                        
                        # Enviar datos
                        send_blood_pressure_data(measurement)
                    else:
                        print("Error en la medición. Intenta de nuevo.")
                        
            elif choice == "2":
                monitor.calibrate_sensor()
                
            elif choice == "3":
                monitor.test_sensor()
                
            elif choice == "4":
                print(f"Estado del sistema:")
                print(f"- Sensor HX710B: Conectado")
                print(f"- Motor L293D: Conectado")
                print(f"- Válvula L293D: Conectada")
                print(f"- Paciente ID: {current_patient_id}")
                print(f"- Doctor ID: {current_doctor_id}")
                print(f"- Mediciones en curso: {is_measuring}")
                
            elif choice == "5":
                break
                
    except KeyboardInterrupt:
        print("\nInterrumpido por el usuario.")
    finally:
        # Limpiar GPIO
        motor_pwm.stop()
        valve_pwm.stop()
        GPIO.cleanup()
        client.loop_stop()
        client.disconnect()
        print("Sistema apagado correctamente.")

if __name__ == "__main__":
    main()