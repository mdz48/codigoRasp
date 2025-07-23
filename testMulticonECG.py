import threading
import time
import json
import serial
import paho.mqtt.client as mqtt
import pika
from smbus2 import SMBus
from max30102 import MAX30102
import hrcalc

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

# ---------- Configuracion Serial para ESP32 ----------
SERIAL_PORT = "/dev/ttyUSB0"
BAUD_RATE = 115200

# Inicializar conexiÃ³n serial
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
        print(f"Revisando configuraciÃ³n: monitoring_active={monitoring_active}, patient_id={current_patient_id}")
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
                print(f"ConfiguraciÃ³n recibida: {config}")
                
                action = config.get("action")
                print(f"AcciÃ³n recibida: {action}")
                
                if action == "start":
                    monitoring_active = True
                    print(f"MONITOREO INICIADO - monitoring_active = {monitoring_active}")
                    publicar_status(f"Monitoreo INICIADO para paciente {config.get('patient_id')}")
                elif action == "stop":
                    monitoring_active = False
                    print(f"MONITOREO DETENIDO - monitoring_active = {monitoring_active}")
                    publicar_status(f"Monitoreo DETENIDO para paciente {config.get('patient_id')}")
                else:
                    print(f"AcciÃ³n no reconocida: {action}")
                
                current_patient_id = config.get("patient_id", current_patient_id)
                current_doctor_id = config.get("doctor_id", current_doctor_id)
                
                print(f"Estado actual: Paciente={current_patient_id}, Doctor={current_doctor_id}, Monitoreo={'ACTIVO' if monitoring_active else 'INACTIVO'}")
                publicar_status(f"Config: Paciente {current_patient_id}, Doctor {current_doctor_id}, Monitoreo: {'ACTIVO' if monitoring_active else 'INACTIVO'}")
                
            except Exception as e:
                print(f"Error en config_callback: {e}")
                publicar_status(f"Error config: {e}")
        
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
                print(f"Temperatura: Monitoreo INACTIVO (monitoring_active={monitoring_active}, patient_id={current_patient_id})")
                publicar_status("Esperando activaciÃ³n de monitoreo y ID paciente para temperatura...")
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
            print(f"Temperatura: {temp}Â°C")
            
        except Exception as e:
            publicar_status(f"Error en sensor de temperatura: {str(e)}")
        time.sleep(1)

def publicar_oxi():
    while True:
        try:
            receive_user_config()
            
            if not monitoring_active or current_patient_id is None:
                print(f"Oximetro: Monitoreo INACTIVO (monitoring_active={monitoring_active}, patient_id={current_patient_id})")
                publicar_status("Esperando activaciÃ³n de monitoreo y ID paciente para oximetro...")
                time.sleep(5)
                continue
            
            print(f"Oximetro: Monitoreo ACTIVO - Publicando datos...")
                
            bpm, spo2 = leer_bpm_spo2(sensor_oxi)
            if bpm is not None and spo2 is not None:
                rabbitmq_data_oxigeno = {
                    "patient_id": current_patient_id,
                    "doctor_id": current_doctor_id,
                    "oxygen_saturation": spo2,
                    "timestamp": timestamp()
                }
                send_to_rabbitmq("oxigeno", rabbitmq_data_oxigeno)
                
                rabbitmq_data_ritmo = {
                    "patient_id": current_patient_id,
                    "doctor_id": current_doctor_id,
                    "heart_rate": bpm,
                    "timestamp": timestamp()
                }
                send_to_rabbitmq("ritmo_cardiaco", rabbitmq_data_ritmo)
                
                mqtt_data = {
                    "patient_id": current_patient_id,
                    "doctor_id": current_doctor_id,
                    "bpm": bpm,
                    "oxygen_saturation": spo2,
                    "timestamp": timestamp()
                }
                client.publish(mqtt_topic_oxi, json.dumps(mqtt_data))
                print(f"BPM: {bpm}, SpO2: {spo2}%")
            else:
                publicar_status("Lecturas de oximetro invalidas")
                
        except Exception as e:
            publicar_status(f"Error en publicar oxigeno: {str(e)}")
        time.sleep(5)

def publicar_ecg():
    print("Hilo ECG iniciado")  # Debug: inicio del hilo
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
                print(f"ECG: Monitoreo INACTIVO (monitoring_active={monitoring_active}, patient_id={current_patient_id})")
                publicar_status("Esperando activación de monitoreo y ID paciente para ECG...")
                time.sleep(1)
                continue

            # Leer todos los datos ECG disponibles rápidamente
            if ser is not None and ser.in_waiting > 0:
                try:
                    while ser.in_waiting > 0:
                        linea = ser.readline().decode("utf-8").strip()
                        print(f"ECG linea recibida: '{linea}' (in_waiting={ser.in_waiting})")  # Debug: datos recibidos
                        if linea.isdigit():
                            buffer.append(int(linea))
                except UnicodeDecodeError:
                    print("Error de codificación al leer ECG")  # Debug: error de codificación
                    pass
                except Exception as e:
                    print(f"Error leyendo ECG: {e}")

            # Enviar datos cada intervalo
            if ahora - ultimo_envio >= intervalo and buffer:
                print(f"ECG: Enviando {len(buffer)} muestras")
                try:
                    # Enviar a RabbitMQ
                    rabbitmq_data = {
                        "patient_id": current_patient_id,
                        "doctor_id": current_doctor_id,
                        "ecg_values": buffer,
                        "timestamp": ahora
                    }
                    send_to_rabbitmq("ecg", rabbitmq_data)
                    print("ECG publicado en RabbitMQ correctamente")  # Debug: éxito RabbitMQ
                except Exception as e:
                    print(f"Fallo al publicar ECG en RabbitMQ: {e}")  # Debug: error RabbitMQ
                try:
                    # Enviar a MQTT
                    mqtt_data = {
                        "patient_id": current_patient_id,
                        "doctor_id": current_doctor_id,
                        "ecg": buffer,
                        "timestamp": ahora
                    }
                    client.publish(mqtt_topic_ecg, json.dumps(mqtt_data))
                    print("ECG publicado en MQTT correctamente")  # Debug: éxito MQTT
                except Exception as e:
                    print(f"Fallo al publicar ECG en MQTT: {e}")  # Debug: error MQTT
                buffer = []
                ultimo_envio = ahora

        except Exception as e:
            print(f"Error en publicar ECG: {str(e)}")  # Debug: error general
            publicar_status(f"Error en publicar ECG: {str(e)}")
            time.sleep(0.1)

        # Lectura muy rápida para capturar todos los datos
        time.sleep(0.005)

def get_sensors_status():
    status = {
        "raspberry": True,
        "MAX30102": False,
        "MLX90614": False,
        "ADB8232": False,  # Cambiado de ESP32_ECG a ADB8232
        "MP520N004D": False,
        "ESP32_ECG": False
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
                status["ESP32_ECG"] = True  # Mantener compatibilidad
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
    if ser is not None:
        ser.close()
    client.loop_stop()
    client.disconnect()
    print("MQTT desconectado")