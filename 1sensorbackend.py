import time
import json
import paho.mqtt.client as mqtt
import pika
from smbus2 import SMBus

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

# ---------- Variables globales ----------
current_patient_id = None
current_doctor_id = None

# ---------- Sensor MLX90614 ----------
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

# ---------- Funcion para enviar a RabbitMQ ----------
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
        print(f"Datos enviados a RabbitMQ - {topic}: {message}")
        connection.close()
    except Exception as e:
        print(f"Error enviando a RabbitMQ: {e}")

# ---------- Funcion para recibir configuracion ----------
def receive_user_config():
    global current_patient_id, current_doctor_id
    try:
        credentials = pika.PlainCredentials(RABBITMQ_USER, RABBITMQ_PASSWORD)
        parameters = pika.ConnectionParameters(host=RABBITMQ_HOST, credentials=credentials)
        connection = pika.BlockingConnection(parameters)
        channel = connection.channel()
        channel.queue_declare(queue='user_config', durable=True)
        channel.queue_bind(exchange=EXCHANGE, queue='user_config', routing_key='user_config')

        def config_callback(ch, method, properties, body):
            global current_patient_id, current_doctor_id
            try:
                config = json.loads(body)
                current_patient_id = config.get("patient_id", current_patient_id)
                current_doctor_id = config.get("doctor_id", current_doctor_id)
                print(f"Configuracion recibida: Paciente {current_patient_id}, Doctor {current_doctor_id}")
            except Exception as e:
                print(f"Error procesando configuracion: {e}")

        channel.basic_consume(queue='user_config', on_message_callback=config_callback, auto_ack=True)
        connection.process_data_events(time_limit=1)
        connection.close()
    except Exception as e:
        print(f"Error recibiendo configuracion: {e}")

# ---------- Funcion para comunicar estatus ----------
def get_sensors_status():
    status = {
        "raspberry": True,
        "MLX90614": False,
        "ADB8232": False,
        "MP520N004D": False
    }
    try:
        _ = sensor_temp.get_object_temp()
        status["MLX90614"] = True
    except Exception:
        pass
    return status

def send_status():
    status = get_sensors_status()
    status["timestamp"] = time.time()
    send_to_rabbitmq("sensor", status)

# ---------- MQTT ----------
client = mqtt.Client()
client.username_pw_set(mqtt_user, mqtt_password)
client.connect(mqtt_host, mqtt_port, keepalive=60)
client.loop_start()

# ---------- Inicializar sensor ----------
sensor_temp = MLX90614()

# ---------- Loop principal ----------
try:
    print(f"Iniciando sensores para Paciente ID: {current_patient_id}")
    if current_doctor_id:
        print(f"Doctor ID: {current_doctor_id}")

    ciclo = 0
    while True:
        receive_user_config()
        if current_patient_id is None:
            print("Esperando configuracion de paciente...")
            time.sleep(2)
            continue

        ciclo += 1
        if ciclo % 5 == 0:
            send_status()

        temperatura = sensor_temp.get_object_temp()

        rabbitmq_temp_data = {
            "patient_id": current_patient_id,
            "doctor_id": current_doctor_id,
            "temperature": temperatura,
            "timestamp": time.time()
        }

        send_to_rabbitmq("temperatura", rabbitmq_temp_data)

        mensaje_temp = json.dumps(rabbitmq_temp_data)
        client.publish(mqtt_topic_temp, mensaje_temp)
        print("Temperatura enviada:", mensaje_temp)

        time.sleep(2)

except KeyboardInterrupt:
    print("Interrumpido por el usuario.")
finally:
    client.loop_stop()
    client.disconnect()
    print("MQTT desconectado.")