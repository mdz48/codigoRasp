# -*- coding: utf-8 -*-
import threading
import time
import json
import serial
import paho.mqtt.client as mqtt
from smbus2 import SMBus
from max30102 import MAX30102
import hrcalc
from collections import deque

# --- Configuración UTF-8 explícita ---
JSON_ENCODING = 'utf-8'
SERIAL_ENCODING = 'utf-8'

# --- Configuración centralizada con tildes correctas ---
CONFIG = {
    "MQTT": {
        "host": "100.28.59.47",
        "port": 1883,
        "user": "admin",
        "password": "password",
        "topics": {
            "temp": "temperatura",
            "oxi": "oxígeno",  # Con tilde correcta
            "ecg": "ecg",
            "status": "estado",
            "config": "configuración"  # Con tilde
        },
        "qos": 1,
        "keepalive": 60
    },
    "SERIAL": {
        "port": "/dev/ttyUSB0",
        "baudrate": 115200,
        "timeout": 0.01,
        "encoding": SERIAL_ENCODING  # Especificamos encoding
    }
}

class MQTTManager:
    def __init__(self):
        self.client = mqtt.Client()
        self.client.username_pw_set(CONFIG["MQTT"]["user"], CONFIG["MQTT"]["password"])
        self.client.on_message = self.on_config_message
        self.client.connect(CONFIG["MQTT"]["host"], CONFIG["MQTT"]["port"], CONFIG["MQTT"]["keepalive"])
        self.client.subscribe(CONFIG["MQTT"]["topics"]["config"], qos=CONFIG["MQTT"]["qos"])
        self.client.loop_start()
        
        self.monitoring_active = False
        self.patient_id = None
        self.doctor_id = None
        self.lock = threading.Lock()
    
    def on_config_message(self, client, userdata, msg):
        try:
            # Decodificación UTF-8 explícita
            config = json.loads(msg.payload.decode(JSON_ENCODING))
            with self.lock:
                self.monitoring_active = config.get("active", False)
                self.patient_id = config.get("patient_id")
                self.doctor_id = config.get("doctor_id")
                
            # Log con tildes correctas
            estado = "activado" if self.monitoring_active else "desactivado"
            print(f"Configuración recibida: Monitoreo {estado} para paciente {self.patient_id}")
            
        except Exception as e:
            print(f"Error al procesar configuración: {str(e)}")
    
    def publish(self, topic_suffix, data):
        topic = CONFIG["MQTT"]["topics"].get(topic_suffix)
        if topic:
            try:
                payload = json.dumps({
                    "patient_id": self.patient_id,
                    "doctor_id": self.doctor_id,
                    "timestamp": time.time(),
                    **data
                }, ensure_ascii=False).encode(JSON_ENCODING)  # UTF-8 explícito
                
                self.client.publish(topic, payload, qos=CONFIG["MQTT"]["qos"])
            except Exception as e:
                print(f"Error al publicar en {topic}: {str(e)}")

class SensorHandler:
    def __init__(self, mqtt_manager):
        self.mqtt = mqtt_manager
        self.serial = serial.Serial(**CONFIG["SERIAL"])
        self.temp_sensor = MLX90614()
        self.oxi_sensor = MAX30102()
        self.ecg_buffer = deque(maxlen=500)
    
    def read_serial_line(self):
        """Lee una línea del serial con manejo robusto de encoding"""
        try:
            line = self.serial.readline().decode(SERIAL_ENCODING).strip()
            return line if line else None
        except UnicodeDecodeError:
            print("Error de decodificación: Se ignoró línea con caracteres inválidos")
            return None
        except Exception as e:
            print(f"Error de lectura serial: {str(e)}")
            return None
    
    def read_ecg(self):
        if not self.mqtt.monitoring_active:
            return
            
        line = self.read_serial_line()
        if line and line.isdigit():
            self.ecg_buffer.append(int(line))
        
        if len(self.ecg_buffer) >= 100:
            self.mqtt.publish("ecg", {"valores_ecg": list(self.ecg_buffer)})
            self.ecg_buffer.clear()

def sensor_thread(name, interval, callback):
    """Hilo optimizado con intervalo preciso"""
    while True:
        start_time = time.time()
        try:
            callback()
        except Exception as e:
            print(f"Error en {name}: {str(e)}")
        
        # Compensación del tiempo de ejecución
        elapsed = time.time() - start_time
        sleep_time = max(0.01, interval - elapsed)  # Mínimo 10ms
        time.sleep(sleep_time)

if __name__ == "__main__":
    print("Iniciando sistema de monitoreo...")
    
    try:
        mqtt_manager = MQTTManager()
        sensors = SensorHandler(mqtt_manager)
        
        threads = [
            threading.Thread(
                target=sensor_thread,
                args=("Temperatura", 1.0, sensors.read_temp),
                daemon=True
            ),
            threading.Thread(
                target=sensor_thread,
                args=("Oxímetro", 5.0, sensors.read_oxi),  # Con tilde
                daemon=True
            ),
            threading.Thread(
                target=sensor_thread,
                args=("ECG", 0.02, sensors.read_ecg),
                daemon=True
            )
        ]
        
        for thread in threads:
            thread.start()
        
        print("Sistema en operación. Presione Ctrl+C para detener.")
        while True:
            time.sleep(1)
            
    except KeyboardInterrupt:
        print("\nDeteniendo el sistema...")
    except Exception as e:
        print(f"Error crítico: {str(e)}")
    finally:
        print("Limpieza completada. Hasta pronto!")