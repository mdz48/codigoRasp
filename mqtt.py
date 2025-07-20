import time
import json
import paho.mqtt.client as mqtt
from smbus2 import SMBus

class MLX90614:
    MLX90614_TOBJ1 = 0x07

    def __init__(self, address=0x5A, bus=1):
        self.bus = SMBus(bus)
        self.address = address

    def read_temp(self, reg):
        temp = self.bus.read_word_data(self.address, reg)
        temp = temp * 0.02 - 273.15
        return round(temp, 2)

    def get_object_temp(self):
        return self.read_temp(self.MLX90614_TOBJ1)

# Configuración MQTT
mqtt_host = "100.28.59.47"
mqtt_port = 1883
mqtt_user = "admin"
mqtt_password = "password"
mqtt_topic = "temperatura"  # <– ¡este topic coincide con tu binding!

# Inicializa MQTT
client = mqtt.Client()
client.username_pw_set(mqtt_user, mqtt_password)
client.connect(mqtt_host, mqtt_port, keepalive=60)
client.loop_start()

# Inicializa el sensor
sensor = MLX90614()

try:
    while True:
        temperatura = sensor.get_object_temp()
        mensaje = json.dumps({
            "objeto": temperatura,
            "timestamp": time.time()
        })
        client.publish(mqtt_topic, mensaje)
        print("Mensaje enviado:", mensaje)
        time.sleep(2)

except KeyboardInterrupt:
    print("Interrumpido por el usuario.")

finally:
    client.loop_stop()
    client.disconnect()
