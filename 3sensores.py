import time
import json
import serial
import threading
import paho.mqtt.client as mqtt
from smbus2 import SMBus
from max30102 import MAX30102
import hrcalc

# MLX90614 Temperature Sensor
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

# MQTT Configuration
MQTT_HOST = "100.28.59.47"
MQTT_PORT = 1883
MQTT_USER = "admin"
MQTT_PASSWORD = "password"

TOPIC_TEMP = "temperature"
TOPIC_OXY = "oxygen"
TOPIC_ECG = "ecg"

# Patient configuration
PATIENT_ID = 1
DOCTOR_ID = None

# Initialize MQTT client
client = mqtt.Client()
client.username_pw_set(MQTT_USER, MQTT_PASSWORD)
client.connect(MQTT_HOST, MQTT_PORT, keepalive=60)
client.loop_start()

# Sensor initialization
temp_sensor = MLX90614()
oxy_sensor = MAX30102()

# ECG Serial Configuration
ECG_PORT = "/dev/ttyUSB0"
ECG_BAUDRATE = 115200
ecg_serial = serial.Serial(ECG_PORT, ECG_BAUDRATE, timeout=1)

def read_bpm_spo2(sensor):
    red_buf = []
    ir_buf = []

    for _ in range(100):
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

def publish_temp_oxy():
    while True:
        # Temperature reading
        temp = temp_sensor.get_object_temp()
        temp_msg = json.dumps({
            "patient_id": PATIENT_ID,
            "doctor_id": DOCTOR_ID,
            "temperature": temp,
            "timestamp": time.time()
        })
        client.publish(TOPIC_TEMP, temp_msg)
        print("Sent temperature:", temp_msg)

        # Oxygen reading
        bpm, spo2 = read_bpm_spo2(oxy_sensor)
        oxy_msg = json.dumps({
            "patient_id": PATIENT_ID,
            "doctor_id": DOCTOR_ID,
            "bpm": bpm,
            "spo2": spo2,
            "timestamp": time.time()
        })
        client.publish(TOPIC_OXY, oxy_msg)
        print("Sent oxygen:", oxy_msg)

        time.sleep(2)

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

# Start threads
thread1 = threading.Thread(target=publish_temp_oxy)
thread2 = threading.Thread(target=publish_ecg)

thread1.start()
thread2.start()

thread1.join()
thread2.join()
