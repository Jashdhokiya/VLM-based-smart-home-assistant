from tuyapy import TuyaApi

from dotenv import load_dotenv
import os

load_dotenv()  # take environment variables from .env

EMAIL = os.getenv("TUYA_EMAIL")
PASSWORD = os.getenv("TUYA_PWD")
COUNTRY_CODE = os.getenv("COUNTRY_CODE")

api = TuyaApi()
api.init(EMAIL, PASSWORD, COUNTRY_CODE) #91 - Country code for India
devices = api.get_all_devices() 

fan1 = devices[2]
light1= devices[1]
light4 = devices[3]
light2 = devices[4]
light3 = devices[0]

def execute(device, command):
    if device == "Light1":
        if command == "On":
            light1.turn_on()
        else:
            light1.turn_off()
    elif device == "Light2":
        if command == "On":
            light2.turn_on()
        else:
            light2.turn_off()
    elif device == "Light3":
        if command == "On":
            light3.turn_on()
        else:
            light3.turn_off()
    elif device == "Light4":
        if command == "On":
            light4.turn_on()
        else:
            light4.turn_off()
    elif device == "Fan1":
        if command == "On":
            fan1.turn_on()
        else:
            fan1.turn_off()
    else:
        print("Invalid device or command")