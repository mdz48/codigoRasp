import time
import json
import serial
import threading
import paho.mqtt.client as mqtt

# MQTT Configuration
MQTT_HOST = "100.28.59.47"
MQTT_PORT = 1883
MQTT_USER = "admin"
MQTT_PASSWORD = "password"

TOPIC_ECG = "ecg"

# Patient configuration
PATIENT_ID = 1
DOCTOR_ID = None

# Initialize MQTT client
client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
client.loop_start()

# ECG Serial Configuration
ECG_PORT = "/dev/ttyUSB0"
ECG_BAUDRATE = 115200
ecg_serial = serial.Serial(ECG_PORT, ECG_BAUDRATE, timeout=1)

def publish_ecg():
    buffer = []
    interval = 0.5
    last_sent = time.time()

    while True:
        try:
            line = ecg_serial.readline().decode("utf-8").strip()
            if line.isdigit():
                buffer.append(int(line))

            now = time.time()
            if now - last_sent >= interval and buffer:
                ecg_msg = json.dumps({
                    "patient_id": PATIENT_ID,
                    "doctor_id": DOCTOR_ID,
                    "ecg": buffer,
                    "timestamp": now
                })
                client.publish(TOPIC_ECG, ecg_msg)
                print("Sent ECG:", ecg_msg)
                buffer = []
                last_sent = now

        except Exception as e:
            print("ECG read error:", e)
        time.sleep(0.005)

# Start ECG publishing in a thread
ecg_thread = threading.Thread(target=publish_ecg)
ecg_thread.start()
ecg_thread.join()
