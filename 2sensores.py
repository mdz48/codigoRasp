import time
import json
import paho.mqtt.client as mqtt
from smbus2 import SMBus
from max30102 import MAX30102
import hrcalc

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

# ---------- MQTT Config ----------
mqtt_host = "100.28.59.47"
mqtt_port = 1883
mqtt_user = "admin"
mqtt_password = "password"
mqtt_topic_temp = "temperatura"
mqtt_topic_oxi = "oxigeno"

client = mqtt.Client()
client.username_pw_set(mqtt_user, mqtt_password)
client.connect(mqtt_host, mqtt_port, keepalive=60)
client.loop_start()

# ---------- Inicializar sensores ----------
sensor_temp = MLX90614()
sensor_oxi = MAX30102()

# ---------- Funcion para obtener BPM y SpO2 ----------
def leer_bpm_spo2(sensor):
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

# ---------- Loop principal ----------
try:
    while True:
        # ??? Lectura temperatura
        temperatura = sensor_temp.get_object_temp()
        mensaje_temp = json.dumps({
            "temperatura": temperatura,
            "timestamp": time.time()
        })
        client.publish(mqtt_topic_temp, mensaje_temp)
        print("??? Temperatura enviada:", mensaje_temp)

        # ?? Lectura MAX30102
        bpm, spo2 = leer_bpm_spo2(sensor_oxi)
        mensaje_oxi = json.dumps({
            "bpm": bpm,
            "spo2": spo2,
            "timestamp": time.time()
        })
        client.publish(mqtt_topic_oxi, mensaje_oxi)
        print("?? Oxigeno enviado:", mensaje_oxi)

        time.sleep(2)

except KeyboardInterrupt:
    print("?? Interrumpido por el usuario.")
finally:
    client.loop_stop()
    client.disconnect()
    print("?? MQTT desconectado.")
