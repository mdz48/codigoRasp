import time
from max30100 import MAX30100

try:
    sensor = MAX30100()
    sensor.enable_spo2()
    print("MAX30100 inicializado")

    while True:
        sensor.read_sensor()
        print("IR:", sensor.ir, "RED:", sensor.red)
        time.sleep(0.5)

except KeyboardInterrupt:
    print("Interrumpido por el usuario")
