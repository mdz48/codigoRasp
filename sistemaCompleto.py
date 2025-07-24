import threading
import time
import json
import pika
import serial
import paho.mqtt.client as mqtt
import math
import statistics
import hrcalc
from smbus2 import SMBus
from max30102 import MAX30102
import RPi.GPIO as GPIO

# ---------- Configuracion RabbitMQ ----------
RABBITMQ_HOST = "100.28.59.47"
RABBITMQ_USER = "admin"
RABBITMQ_PASSWORD = "password"
EXCHANGE = 'amq.topic'

# ---------- Configuracion MQTT ----------
mqtt_host = "100.28.59.47"
mqtt_port = 1883
mqtt_user = "admin"
mqtt_password = "password"
mqtt_topic_temp = "temperatura"
mqtt_topic_oxi = "oxigeno"
mqtt_topic_status = "estado"
mqtt_topic_ecg = "ecg"
mqtt_topic_ritmo = "ritmo_cardiaco"
mqtt_topic_presion = "presion"

# ---------- Configuracion Serial para ESP32 ----------
SERIAL_PORT = "/dev/ttyUSB0"
BAUD_RATE = 115200

# ---------- Configuración de pines para presión arterial ----------
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

# Inicializar conexión serial
try:
    ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
    print(f"Conectado a ESP32 en {SERIAL_PORT}")
except Exception as e:
    print(f"Error conectando a ESP32: {e}")
    ser = None

# ---------- Variables globales ----------
current_patient_id = None
current_doctor_id = None
monitoring_active = False

# ---------- Clase MLX90614 ----------
class MLX90614:
    MLX90614_TOBJ1 = 0x07

    def __init__(self, address=0x5A, bus=1):
        self.bus = SMBus(bus)
        self.address = address

    def read_temp(self, reg):
        temp = self.bus.read_word_data(self.address, reg)
        return round(temp * 0.02 - 273.15, 2)

    def get_object_temp(self):
        return self.read_temp(self.MLX90614_TOBJ1)

# ---------- Clase HX710B para presión arterial ----------
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
        sistolica_temp = None
        diastolica_temp = None
        # Buscar sistólica (antes del máximo) - valor más ALTO
        for i in range(1, idx_max):
            if diffs[i] > umbral:
                sistolica_temp = presiones_suavizadas[i]
                break
        # Buscar diastólica (después del máximo) - valor más BAJO
        for i in range(idx_max, len(diffs)):
            if diffs[i] < umbral:
                diastolica_temp = presiones_suavizadas[i]
                break
        
        # Corregir interpretación: sistólica debe ser mayor que diastólica
        if sistolica_temp and diastolica_temp:
            if sistolica_temp > diastolica_temp:
                sistolica = sistolica_temp
                diastolica = diastolica_temp
            else:
                # Si están invertidas, corregir
                sistolica = diastolica_temp
                diastolica = sistolica_temp
        else:
            sistolica = sistolica_temp
            diastolica = diastolica_temp
            
        resultados[umbral_pct] = (sistolica, diastolica)
    print("\nResultados para diferentes umbrales:")
    for pct, (sis, dias) in resultados.items():
        print(f"  Umbral {int(pct*100)}%: Sistólica={sis:.1f} mmHg, Diastólica={dias:.1f} mmHg" if sis and dias else f"  Umbral {int(pct*100)}%: No detectada")
    # Devuelve el resultado para 50% (clásico)
    return resultados[0.5]

# ---------- Medición automática de presión arterial ----------
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
    
    # Fase de inflado
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
                
    # Mantener presión objetivo 6 segundos
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
    
    # Desinflado controlado
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
            
    valvula_abrir()  # Liberar presión al final
    print("\nMedición finalizada. Procesando datos...")
    
    # Procesamiento oscilométrico
    sistolica, diastolica = detectar_sistolica_diastolica_oscilometrico(presiones)
    
    # Los valores ya están corregidos en el algoritmo oscilométrico
        
    print(f"\n--- Resultados ---")
    print(f"Presión Sistólica estimada: {sistolica:.1f} mmHg" if sistolica else "No detectada")
    print(f"Presión Diastólica estimada: {diastolica:.1f} mmHg" if diastolica else "No detectada")
    
    return sistolica, diastolica

# ---------- Configuracion MQTT ----------
client = mqtt.Client()
client.username_pw_set(mqtt_user, mqtt_password)
client.connect(mqtt_host, mqtt_port, 60)
client.loop_start()

# ---------- Inicializar sensores ----------
sensor_temp = MLX90614()
sensor_oxi = MAX30102()

def timestamp():
    return time.time()

def publicar_status(msg):
    payload = {"status": msg, "timestamp": timestamp()}
    client.publish(mqtt_topic_status, json.dumps(payload))
    print(f"Status: {msg}")

def send_to_rabbitmq(topic, data):
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
        print(f"RabbitMQ - {topic}: {message}")
        connection.close()
    except Exception as e:
        publicar_status(f"Error RabbitMQ: {str(e)}")

def receive_user_config():
    global current_patient_id, current_doctor_id, monitoring_active
    try:
        print(f"Revisando configuración: monitoring_active={monitoring_active}, patient_id={current_patient_id}")
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD)
        parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials)
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        
        channel.queue_declare(queue='user_config', durable=True)
        channel.queue_bind(exchange=EXCHANGE, queue='user_config', routing_key='user_config')
        
        def config_callback(ch, method, properties, body):
            global current_patient_id, current_doctor_id, monitoring_active
            try:
                config = json.loads(body)
                print(f"Configuración recibida: {config}")
                
                if config.get("action") == "start":
                    current_patient_id = config.get("patient_id")
                    current_doctor_id = config.get("doctor_id")
                    monitoring_active = True
                    publicar_status(f"Monitoreo iniciado para paciente {current_patient_id}")
                elif config.get("action") == "stop":
                    monitoring_active = False
                    publicar_status(f"Monitoreo detenido para paciente {current_patient_id}")
                    current_patient_id = None
                    current_doctor_id = None
                    
            except Exception as e:
                publicar_status(f"Error procesando config: {e}")
        
        channel.basic_consume(queue='user_config', on_message_callback=config_callback, auto_ack=True)
        connection.process_data_events(time_limit=1)
        connection.close()
        
    except Exception as e:
        publicar_status(f"Error recibiendo config: {e}")

def leer_bpm_spo2(sensor):
    try:
        red_buf = []
        ir_buf = []

        for i in range(100):
            red, ir = sensor.read_fifo()
            red_buf.append(red)
            ir_buf.append(ir)
            time.sleep(0.03)

        bpm, valid_bpm, spo2, valid_spo2 = hrcalc.calc_hr_and_spo2(ir_buf, red_buf)

        if not valid_bpm:
            bpm = 0
        if not valid_spo2:
            spo2 = 0

        return round(bpm, 2), round(spo2, 2)
    except Exception as e:
        publicar_status(f"Error MAX30102: {str(e)}")
        return None, None

def publicar_temp():
    while True:
        try:
            receive_user_config()
            
            if not monitoring_active or current_patient_id is None:
                time.sleep(1)
                continue
            
            print(f"Temperatura: Monitoreo ACTIVO - Publicando datos...")
                
            temp = sensor_temp.get_object_temp()
            
            rabbitmq_data = {
                "patient_id": current_patient_id,
                "doctor_id": current_doctor_id,
                "temperature": temp,
                "timestamp": timestamp()
            }
            send_to_rabbitmq("temperatura", rabbitmq_data)
            
            mqtt_data = {
                "patient_id": current_patient_id,
                "doctor_id": current_doctor_id,
                "temperature": temp,
                "timestamp": timestamp()
            }
            client.publish(mqtt_topic_temp, json.dumps(mqtt_data))
            print(f"Temperatura: {temp}°C")
            
        except Exception as e:
            publicar_status(f"Error en sensor de temperatura: {str(e)}")
        time.sleep(1)

def publicar_oxi():
    while True:
        try:
            receive_user_config()
            
            if not monitoring_active or current_patient_id is None:
                time.sleep(5)
                continue
            
            print(f"Oximetro: Monitoreo ACTIVO - Publicando datos...")
                
            bpm, spo2 = leer_bpm_spo2(sensor_oxi)
            if bpm is not None and spo2 is not None:
                rabbitmq_data = {
                    "patient_id": current_patient_id,
                    "doctor_id": current_doctor_id,
                    "heart_rate": bpm,
                    "oxygen_saturation": spo2,
                    "timestamp": timestamp()
                }
                send_to_rabbitmq("oxigeno", rabbitmq_data)
                send_to_rabbitmq("ritmo_cardiaco", rabbitmq_data)
                
                mqtt_data = {
                    "patient_id": current_patient_id,
                    "doctor_id": current_doctor_id,
                    "heart_rate": bpm,
                    "oxygen_saturation": spo2,
                    "timestamp": timestamp()
                }
                client.publish(mqtt_topic_oxi, json.dumps(mqtt_data))
                client.publish(mqtt_topic_ritmo, json.dumps(mqtt_data))
                print(f"BPM: {bpm}, SpO2: {spo2}%")
            else:
                print("Error leyendo oxímetro")
                
        except Exception as e:
            publicar_status(f"Error en publicar oxigeno: {str(e)}")
        time.sleep(5)

def publicar_presion():
    """Hilo para medir presión arterial cada 30 minutos"""
    while True:
        try:
            receive_user_config()
            
            if not monitoring_active or current_patient_id is None:
                time.sleep(60)  # Revisar cada minuto si no está activo
                continue
            
            print(f"Presión: Monitoreo ACTIVO - Iniciando medición de presión arterial...")
            
            # Medir presión arterial automáticamente con velocidad 100
            sistolica, diastolica = medir_presion_automatica_pulsos(velocidad=100)
            
            # Enviar datos aunque solo se detecte uno de los valores
            if sistolica or diastolica:
                # Si no se detecta un valor, asignar 0
                sistolica_valor = sistolica if sistolica else 0
                diastolica_valor = diastolica if diastolica else 0
                
                # Formato de presión arterial sistólica/diastólica
                blood_pressure = f"{sistolica_valor:.0f}/{diastolica_valor:.0f}"
                
                rabbitmq_data = {
                    "patient_id": current_patient_id,
                    "doctor_id": current_doctor_id,
                    "blood_pressure": blood_pressure,
                    "sistolica": sistolica_valor,
                    "diastolica": diastolica_valor,
                    "timestamp": timestamp()
                }
                send_to_rabbitmq("presion", rabbitmq_data)
                
                mqtt_data = {
                    "patient_id": current_patient_id,
                    "doctor_id": current_doctor_id,
                    "blood_pressure": blood_pressure,
                    "sistolica": sistolica_valor,
                    "diastolica": diastolica_valor,
                    "timestamp": timestamp()
                }
                client.publish(mqtt_topic_presion, json.dumps(mqtt_data))
                
                # Mostrar información sobre valores detectados
                if sistolica and diastolica:
                    print(f"Presión Arterial: {blood_pressure} mmHg (ambos valores detectados)")
                elif sistolica:
                    print(f"Presión Arterial: {blood_pressure} mmHg (solo sistólica detectada)")
                elif diastolica:
                    print(f"Presión Arterial: {blood_pressure} mmHg (solo diastólica detectada)")
            else:
                print("Error: No se pudo detectar ningún valor de presión arterial")
                
        except Exception as e:
            publicar_status(f"Error en medición de presión: {str(e)}")
        
        # Esperar 30 minutos antes de la siguiente medición
        time.sleep(1800)  # 30 minutos = 1800 segundos

def publicar_ecg():
    print("Hilo ECG iniciado")
    buffer = []
    intervalo = 0.5  # Enviar cada 0.5 segundos
    ultimo_envio = time.time()
    ultimo_config_check = time.time()
    
    while True:
        try:
            ahora = time.time()
            # Verificar configuración cada 2 segundos
            if ahora - ultimo_config_check >= 2:
                receive_user_config()
                ultimo_config_check = ahora

            if not monitoring_active or current_patient_id is None:
                time.sleep(0.1)
                continue

            # Leer todos los datos ECG disponibles rápidamente
            if ser is not None and ser.in_waiting > 0:
                try:
                    line = ser.readline().decode('utf-8').strip()
                    if line and line.replace('.', '').replace('-', '').isdigit():
                        valor_ecg = float(line)
                        buffer.append({
                            "value": valor_ecg,
                            "timestamp": ahora
                        })
                except Exception as e:
                    print(f"Error leyendo ECG: {e}")

            # Enviar datos cada intervalo
            if ahora - ultimo_envio >= intervalo and buffer:
                rabbitmq_data = {
                    "patient_id": current_patient_id,
                    "doctor_id": current_doctor_id,
                    "ecg_data": buffer,
                    "timestamp": timestamp()
                }
                send_to_rabbitmq("ecg", rabbitmq_data)
                
                mqtt_data = {
                    "patient_id": current_patient_id,
                    "doctor_id": current_doctor_id,
                    "ecg_data": buffer,
                    "timestamp": timestamp()
                }
                client.publish(mqtt_topic_ecg, json.dumps(mqtt_data))
                print(f"ECG: {len(buffer)} muestras enviadas")
                
                buffer = []
                ultimo_envio = ahora

        except Exception as e:
            print(f"Error en publicar ECG: {str(e)}")
            publicar_status(f"Error en publicar ECG: {str(e)}")
            time.sleep(0.1)

        # Lectura muy rápida para capturar todos los datos
        time.sleep(0.005)

def get_sensors_status():
    status = {
        "raspberry": True,
        "MAX30102": False,
        "MLX90614": False,
        "ADB8232": False,
        "MP520N004D": False,
        "ESP32_ECG": False,
        "HX710B": False
    }
    
    # Probar MLX90614
    try:
        _ = sensor_temp.get_object_temp()
        status["MLX90614"] = True
    except Exception:
        pass
        
    # Probar MAX30102
    try:
        bpm, spo2 = leer_bpm_spo2(sensor_oxi)
        if bpm is not None and spo2 is not None:
            status["MAX30102"] = True
    except Exception:
        pass
    
    # Probar ADB8232 (ESP32 ECG)
    try:
        if ser is not None and ser.is_open:
            # Verificar si hay datos disponibles
            if ser.in_waiting > 0:
                status["ADB8232"] = True
                status["ESP32_ECG"] = True
    except Exception:
        pass
    
    # Probar HX710B (sensor de presión)
    try:
        sensor_presion = HX710B(HX710B_DOUT, HX710B_SCK)
        presion = sensor_presion.read_pressure_mmhg()
        if presion is not None:
            status["HX710B"] = True
    except Exception:
        pass
    
    return status

def publicar_status_sensores():
    while True:
        try:
            status = get_sensors_status()
            status["timestamp"] = timestamp()
            
            send_to_rabbitmq("sensor", status)
            client.publish(mqtt_topic_status, json.dumps(status))
            print(f"Status sensores: {status}")
            
        except Exception as e:
            publicar_status(f"Error en status sensores: {str(e)}")
        time.sleep(10)

def ejecutar_con_reintento(nombre, funcion):
    while True:
        try:
            publicar_status(f"Iniciando hilo {nombre}")
            funcion()
        except Exception as e:
            publicar_status(f"Error critico en hilo {nombre}: {str(e)}. Reiniciando...")
            time.sleep(5)

# ---------- Inicializar sistema ----------
try:
    publicar_status("Sistema iniciando...")
    print(f"Estado inicial: monitoring_active={monitoring_active}, patient_id={current_patient_id}, doctor_id={current_doctor_id}")
    
    # Lanzar hilos con autoreinicio
    hilos = [
        threading.Thread(target=ejecutar_con_reintento, args=("Temperatura", publicar_temp), daemon=True),
        threading.Thread(target=ejecutar_con_reintento, args=("Oximetro", publicar_oxi), daemon=True),
        threading.Thread(target=ejecutar_con_reintento, args=("ECG", publicar_ecg), daemon=True),
        threading.Thread(target=ejecutar_con_reintento, args=("Presion", publicar_presion), daemon=True),
        threading.Thread(target=ejecutar_con_reintento, args=("Status", publicar_status_sensores), daemon=True)
    ]

    for hilo in hilos:
        hilo.start()
        
    publicar_status("Todos los hilos iniciados correctamente")
    
    # Mantener el programa principal corriendo
    while True:
        time.sleep(1)
        
except KeyboardInterrupt:
    publicar_status("Sistema detenido por usuario")
    print("\nCerrando sistema...")
finally:
    # Limpiar GPIO y cerrar conexiones
    try:
        motor_parar()
        valvula_abrir()
        pwm_motor.stop()
        GPIO.cleanup()
        print("GPIO liberado")
    except:
        pass
        
    if ser is not None:
        ser.close()
    client.loop_stop()
    client.disconnect()
    print("Sistema cerrado correctamente")