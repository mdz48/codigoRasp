import threading
import time
import json
import paho.mqtt.client as mqtt
from datetime import datetime


from max30102 import MAX30102
from heartrate_monitor import HeartRateMonitor

import serial

# ------------ Configuracion MQTT ------------
mqtt_host = "100.28.59.47"
mqtt_port = 1883
mqtt_user = "admin"
mqtt_password = "password"
mqtt_topic_temp = "temperatura"
mqtt_topic_oxi = "oxigeno"
mqtt_topic_status = "estado"
mqtt_topic_ecg = "ECG"

client = mqtt.Client()
client.username_pw_set(mqtt_user, mqtt_password)
client.connect(mqtt_host, mqtt_port, 60)

# Sensor temperatura
sensor_temp = MLX90614()

# Sensor oximetro
sensor_oxi = MAX30102()
hrm = HeartRateMonitor(sensor_oxi)

# Serial con ESP32
ser = serial.Serial('/dev/ttyUSB0', 9600, timeout=1)
ser.flush()

def timestamp():
    return time.time()

def publicar_status(msg):
    payload = {"status": msg, "timestamp": timestamp()}
    client.publish(mqtt_topic_status, json.dumps(payload))

def publicar_temp():
    while True:
        try:
            temp = sensor_temp.get_object_1()
            payload = {"temperatura": temp, "timestamp": timestamp()}
            client.publish(mqtt_topic_temp, json.dumps(payload))
        except Exception as e:
            publicar_status(f"Error en sensor de temperatura: {str(e)}")
        time.sleep(1)

def leer_bpm_spo2(sensor):
    try:
        red, ir = sensor.read_fifo()
        bpm, spo2 = hrm.update(ir, red)
        return bpm, spo2
    except Exception as e:
        publicar_status(f"Error en MAX30102: {str(e)}")
        return None, None

def publicar_oxi():
    while True:
        try:
            bpm, spo2 = leer_bpm_spo2(sensor_oxi)
            if bpm is not None and spo2 is not None:
                payload = {"bpm": bpm, "spo2": spo2, "timestamp": timestamp()}
                client.publish(mqtt_topic_oxi, json.dumps(payload))
        except Exception as e:
            publicar_status(f"Error en publicar oxígeno: {str(e)}")
        time.sleep(2)

def publicar_ecg():
    while True:
        try:
            if ser.in_waiting > 0:
                line = ser.readline().decode('utf-8').rstrip()
                payload = {"ecg": line, "timestamp": timestamp()}
                client.publish(mqtt_topic_ecg, json.dumps(payload))
        except Exception as e:
            publicar_status(f"Error leyendo ECG desde ESP32: {str(e)}")
        time.sleep(0.1)

def ejecutar_con_reintento(nombre, funcion):
    while True:
        try:
            publicar_status(f"Iniciando hilo {nombre}")
            funcion()
        except Exception as e:
            publicar_status(f"Error crítico en hilo {nombre}: {str(e)}. Reiniciando...")
            time.sleep(2)

# Lanzar hilos con autoreinicio
hilos = [
    threading.Thread(target=ejecutar_con_reintento, args=("Temp", publicar_temp)),
    threading.Thread(target=ejecutar_con_reintento, args=("Oxi", publicar_oxi)),
    threading.Thread(target=ejecutar_con_reintento, args=("ECG", publicar_ecg))
]

for hilo in hilos:
    hilo.start()

for hilo in hilos:
    hilo.join()
